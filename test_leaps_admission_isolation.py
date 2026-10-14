"""Real provider/service/native store/ASGI qualification, with synthetic wire IO."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal as D
from fractions import Fraction
import json
import os
import multiprocessing
from pathlib import Path
import threading

import pytest
import api
from modules.atomic_cache import write_json_atomic
from modules.leaps.api_contract import LeapsApiService
from modules.leaps.admission import OSLease
from modules.leaps.provider import MassiveLeapsProvider
from modules.leaps.runtime import LeapsRunner, LeapsStore
from modules.leaps.service import DiscoveryProvider
from tests.fixtures.leaps.api import certified
from tests.fixtures.leaps.discovery import term_producer, session
from tests.fixtures.leaps.provider import NOW, capability, run, chain_raw, raw_contract
from tests.fixtures.leaps.whole_flow import WholeFlowTransport
from test_leaps_api import auth, enabled, request


def flow(monkeypatch, tmp_path, transport):
    # Only IO is replaced: real provider JSON parser, discovery, native leases,
    # checkpoint, atomic store and API projection all remain in the path.
    gate = certified(tmp_path)
    market = MassiveLeapsProvider(transport, lambda: NOW, gate, capability())
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    runner = LeapsRunner(DiscoveryProvider(market, terms_producer=term_producer,
                                          session_producer=session), store, gate, lambda: NOW)
    access = tmp_path/'access'
    access.mkdir()
    enabled(monkeypatch, access)
    service = LeapsApiService(store, runner=runner, clock_ns=lambda: NOW)
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', lambda: service)
    return runner, service


def finish(runner):
    runner.start(run())
    return runner.run_slice()


def scenario_body(payload, **extra):
    return dict(run_id=payload['run_id'], snapshot_id=payload['snapshot_id'],
                contract_id=payload['rows'][0]['contract']['id'], quantity=3, **extra)


@pytest.mark.parametrize('stock,valid', [
    ({'T':'SOFI','p':20,'P':21,'s':200,'S':300}, False),
    ({'T':'SOFI','p':20,'P':19,'s':200,'S':300,'y':NOW-1}, False),
    ({'T':'SOFI','p':20,'P':21,'s':200,'S':300,'y':NOW+1}, False),
    ({'T':'SOFI','p':20,'P':21,'s':200,'S':300,'y':NOW-120*10**9}, True),
    ({'T':'SOFI','p':20,'s':200,'y':NOW-1}, True),
])
def test_original_stock_source_controls_relative_math_not_option_ask(monkeypatch,tmp_path,stock,valid):
    runner,_ = flow(monkeypatch,tmp_path,WholeFlowTransport(stock=stock))
    snapshot = finish(runner)
    response = request('/api/leaps/results')
    assert response.status_code == 200
    payload = response.json()
    row = payload['rows'][0]
    assert row['stock_reference']['price'] == ('20' if valid else None)
    assert row['be_rise'] == ('0.01' if valid else None)
    assert row['observation']['quote']['ask'] == '5.2'
    assert row['observation']['quote']['source_ns'] == str(NOW-1)
    assert row['assessment']['comparison_eligible'] is False
    assert row['assessment']['algebra_eligible'] is True
    assert len(row['scenarios']) == (6 if valid else 1)
    result = request('/api/leaps/scenarios', method='POST', body=scenario_body(payload,terminal_prices=['25'])).json()
    expiry = result['scenarios'][0]['result']
    assert D(expiry['debit']) == 1560 and D(expiry['intrinsic']) == 3000 and D(expiry['pnl']) == 1440
    assert expiry['stock_pnl'] == ('1500' if valid else None)
    assert expiry['stock_price_roi'] == ('0.25' if valid else None)
    assert result['quote_source_ns'] == str(NOW-1)
    defaults = request('/api/leaps/scenarios', method='POST', body=scenario_body(payload)).json()
    assert len(defaults['scenarios']) == (6 if valid else 1)
    assert snapshot.sources[0]['request_id'] == 'reference-wire-1'


@pytest.mark.parametrize('change,expected,source', [
    ('newer', '6.2', NOW), ('older', '5.2', NOW-1),
    ('metadata', '5.2', NOW-1), ('same', '5.2', NOW-1),
    ('equal-conflict', None, None), ('terms-conflict', None, None),
])
def test_original_quote_evolution_is_not_raw_mapping_conflict(monkeypatch,tmp_path,change,expected,source):
    original, update = chain_raw(), chain_raw()
    if change in ('newer','older','equal-conflict'):
        update['last_quote']['ask'] = D('6.2')
        update['last_quote']['last_updated'] = NOW if change=='newer' else NOW-2 if change=='older' else NOW-1
    elif change=='metadata':
        update['day']['volume'] = 999
        update['greeks']['delta'] = D('.7')
    elif change=='terms-conflict':
        update['details']['shares_per_contract'] = 10
    transport = WholeFlowTransport(chain_pages=[[original],[update],[original]])
    runner,_ = flow(monkeypatch,tmp_path,transport)
    snapshot = finish(runner)
    payload = request('/api/leaps/results').json()
    assert payload['total'] == (0 if expected is None else 1)
    assert snapshot.coverage['chain_observed'] == 1
    assert [item['request_id'] for item in snapshot.sources if item['scope']=='chain:SOFI'] == ['chain-wire-0','chain-wire-1','chain-wire-2']
    if expected is not None:
        row = payload['rows'][0]
        assert row['observation']['quote']['ask'] == expected
        assert row['observation']['quote']['source_ns'] == str(source)
        assert row['contract']['shares'] == 100 and row['contract']['premium_multiplier'] == '100'
        assert row['terms_fingerprint'] == row['contract']['terms_fingerprint']
        response = request('/api/leaps/scenarios',method='POST',body=scenario_body(payload,terminal_prices=['25']))
        assert response.status_code == 200
        result = response.json()['scenarios'][0]['result']
        assert Fraction(result['debit']) == Fraction(expected)*300
        assert Fraction(result['pnl']) == Fraction(3000)-Fraction(expected)*300
    else:
        assert snapshot.coverage['excluded'] == 1


def _legacy_bytes():
    # The launcher's private paths, never a user's database. Catch real record
    # insertion even if an integration bypasses a known API dispatch function.
    keys=('SIGNAL_TRACKER_DB_PATH','SIGNAL_DELIVERY_JOURNAL_DB_PATH',
          'MAIL_OUTBOX_DB_PATH','EMAIL_DEDUPE_FILE','SUPPRESSION_TELEMETRY_DB_PATH')
    paths=[Path(os.environ[key]) for key in keys]
    root=Path(os.environ['ALPHA_DATA_DIR'])
    if root.exists():
        paths.extend(p for p in root.rglob('*') if p.is_file() and 'leaps' not in p.relative_to(root).parts)
    return {str(p): p.read_bytes() if p.exists() else None for p in paths}


@pytest.mark.parametrize('kind',['normal','duplicate','adjusted','failed'])
def test_leaps_flow_never_records_legacy_mail_weekly_pf_or_autotrader(monkeypatch,tmp_path,kind):
    raw=chain_raw()
    refs=[raw_contract()]
    pages=[[raw,deepcopy(raw)]] if kind=='duplicate' else [[raw]]
    if kind=='adjusted':
        refs[0]['shares_per_contract']=10
    if kind=='failed':
        pages=[ConnectionError('synthetic outage')]
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport(chain_pages=pages,references=refs))
    def forbidden(*args,**kwargs):
        pytest.fail('LEAPS escaped into a legacy mail/tracker/autotrader path')
    for name in ('_send_email_alert','record_signal','autotrader_scan_once'):
        if hasattr(api,name):
            monkeypatch.setattr(api,name,forbidden)
    before=_legacy_bytes()
    snapshot=finish(runner)
    response=request('/api/leaps/results')
    assert response.status_code==200
    payload=response.json()
    if payload['rows']:
        assert request('/api/leaps/scenarios',method='POST',body=scenario_body(payload,terminal_prices=['25'])).status_code==200
    assert snapshot.status in ('complete','partial')
    assert _legacy_bytes()==before
    assert 'cursor' not in payload


@pytest.mark.parametrize('next_action',['resume','scan','new_runner'])
def test_http_pause_during_io_retains_native_owner_and_blocks_retry(monkeypatch,tmp_path,next_action):
    entered,release=threading.Event(),threading.Event()
    def before(url):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
            raise ConnectionError('synthetic interrupted first attempt')
    transport=WholeFlowTransport(before=before)
    runner,service=flow(monkeypatch,tmp_path,transport)
    response=request('/api/leaps/scan',method='POST',body={'run_id':'pause-retry'})
    try:
        assert response.status_code==200 and entered.wait(2)
        paused=request('/api/leaps/control',method='POST',body={'run_id':'pause-retry','action':'pause'})
        assert paused.status_code==200 and paused.json()['status']=='paused'
        contender=OSLease(runner.store.root/'request.lock')
        acquired=contender.acquire()
        try:
            assert not acquired, 'pause acknowledgement must not release physical IO owner'
        finally:
            if acquired:
                contender.release()
    finally:
        release.set()
        service._worker.join(5)
    assert not service._worker.is_alive()
    assert runner.snapshot().status=='paused'
    assert len(transport.calls)==1, 'paused run must not start a new physical retry'
    with OSLease(runner.store.root/'request.lock'):
        pass
    # The call-local guard is gone after unwinding: an explicitly unbound direct
    # provider use does not inherit this runner's paused action.
    raw_provider=runner.provider.market.provider
    assert raw_provider.list_contract_page(replace(run(),run_id='unbound-probe')).complete
    service.clock_ns=lambda:NOW+60*10**9
    if next_action=='new_runner':
        replacement=LeapsRunner(runner.provider,runner.store,runner.admission,lambda:NOW)
        service.runner=replacement
        runner=replacement
    if next_action=='resume':
        response=request('/api/leaps/control',method='POST',body={'run_id':'pause-retry','action':'resume'})
    else:
        response=request('/api/leaps/scan',method='POST',body={'run_id':'new-run'})
    assert response.status_code==200
    service._worker.join(5)
    assert runner.snapshot().status=='complete'
    assert runner.snapshot().run.run_id==('pause-retry' if next_action=='resume' else 'new-run')


def test_concurrent_read_clock_watermarks_never_regress_or_rewrite_blob(monkeypatch,tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    finish(runner)
    before={p.name:p.read_bytes() for p in runner.store.blobs.iterdir() if p.is_file()}
    stores=[LeapsStore(tmp_path,write_json_atomic,clock_ns=lambda:NOW) for _ in range(4)]
    offsets=(61,5,200,3,150,100,250,10)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda pair:stores[pair[0]%4].observe_clock(lambda:NOW+pair[1]*10**9),enumerate(offsets)))
    assert json.loads(runner.store.path.read_text())['clock_watermark_ns']==str(NOW+250*10**9)
    assert all(store.observe_clock(lambda:NOW+60*10**9)[1] for store in stores)
    assert {p.name:p.read_bytes() for p in runner.store.blobs.iterdir() if p.is_file()}==before


@pytest.mark.parametrize('bad',[None,D('1e308'),D('NaN')])
def test_inherited_indicator_consumer_publishes_only_finite_completed_evidence(monkeypatch,tmp_path,bad):
    from datetime import date,timedelta
    from modules.leaps.underlying import build_underlying_evidence
    from modules.leaps.models import LeapsSnapshot
    bars=[dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),completed=True,
               open=D(i+20),high=D(i+21),low=D(i+19),close=D(i+20),volume=D(1000)) for i in range(250)]
    if bad is not None:
        bars[-1]['close']=bad
    evidence=build_underlying_evidence('SOFI',completed_bars=bars,facts=None,sources=('synthetic-completed',),as_of=run().market_date)
    if bad is None:
        assert evidence.coverage['history']=='complete'
        assert evidence.indicators['ema200'] is not None
    else:
        assert evidence.coverage['history']=='invalid'
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    snapshot=finish(runner)
    row=dict(snapshot.data['comparisons'][0],underlying=evidence)
    runner.store.publish(replace(snapshot,data=dict(snapshot.data,comparisons=(row,))))
    response=request('/api/leaps/results')
    assert response.status_code==200
    assert 'NaN' not in response.text and 'Infinity' not in response.text
    json.loads(response.text,parse_constant=lambda value:pytest.fail('nonfinite public payload'))


@pytest.mark.parametrize('failure',['fsync','replace'])
def test_inherited_atomic_publication_failure_preserves_last_complete(monkeypatch,tmp_path,failure):
    import modules.atomic_cache as atomic
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    snapshot=finish(runner)
    before=runner.store.path.read_bytes()
    def fail(*args,**kwargs):
        raise OSError('synthetic atomic failure')
    with monkeypatch.context() as patch:
        patch.setattr(atomic.os,failure,fail)
        with pytest.raises(OSError):
            runner.store.publish(replace(snapshot,status='partial',reasons=('synthetic_failure',)))
    assert runner.store.path.read_bytes()==before
    assert not list(runner.store.root.glob('.state.json.*.tmp'))
    payload=request('/api/leaps/results').json()
    assert payload['status']=='complete' and payload['rows'][0]['observation']['quote']['ask']=='5.2'


def test_slow_snapshot_decode_does_not_block_status_or_pause(monkeypatch,tmp_path):
    import modules.leaps.runtime as runtime
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    final=finish(runner)
    runner.store.publish(replace(final,status='running'))
    entered,release=threading.Event(),threading.Event()
    original=runtime._decode
    def slow(value):
        if isinstance(value,dict) and value.get('t')=='LeapsSnapshot' and not entered.is_set():
            entered.set()
            assert release.wait(6)
        return original(value)
    monkeypatch.setattr(runtime,'_decode',slow)
    response=[]
    reader=threading.Thread(target=lambda:response.append(request('/api/leaps/results')))
    reader.start()
    try:
        assert entered.wait(2)
        status=request('/api/leaps/status')
        paused=request('/api/leaps/control',method='POST',body={'run_id':run().run_id,'action':'pause'})
        assert status.status_code==200 and paused.status_code==200
        assert paused.json()['status']=='paused'
    finally:
        release.set()
        reader.join(5)
    assert response[0].status_code==200


def test_legacy_v1_migrates_preserving_last_complete_and_original_source(monkeypatch,tmp_path):
    import modules.leaps.runtime as runtime
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    final=finish(runner)
    legacy={'version':1,'latest':runtime._encode(replace(final,status='paused')),
            'last_complete':runtime._encode(final),'control':{'run_id':run().run_id,'action':'pause'},
            'authorities':{},'revision':3,'saved_ns':str(NOW)}
    write_json_atomic(runner.store.path,legacy)
    fresh=LeapsStore(tmp_path,write_json_atomic,clock_ns=lambda:NOW)
    assert fresh.load_complete()==final
    assert fresh.load().status=='paused'
    fresh.publish(replace(final,status='running'))
    raw=json.loads(fresh.path.read_text())
    assert raw['version']==2
    assert fresh.load_complete()==final
    assert fresh.load().data['comparisons'][0]['observation'].quote.source_ns==NOW-1


@pytest.mark.parametrize('damage',['changed_same_size_and_mtime','missing','partial'])
def test_warm_immutable_cache_never_masks_missing_or_changed_blob(monkeypatch,tmp_path,damage):
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    finish(runner)
    assert request('/api/leaps/results').status_code==200
    meta=json.loads(runner.store.path.read_text())
    assert meta['version']==2
    assert meta['latest']['blob']==meta['last_complete']['blob']
    blob=runner.store.root/'snapshots'/(meta['latest']['blob']+'.json')
    stat=blob.stat()
    if damage=='missing':
        blob.unlink()
    elif damage=='partial':
        blob.write_bytes(b'{')
    else:
        original=blob.read_bytes()
        changed=original.replace(b'"5.2"',b'"5.3"')
        assert changed!=original and len(changed)==len(original)
        blob.write_bytes(changed)
        os.utime(blob,ns=(stat.st_atime_ns,stat.st_mtime_ns))
    assert request('/api/leaps/results').status_code==503


def test_unchanged_contract_math_not_rebuilt_each_page_and_expiry_not_renewed(monkeypatch,tmp_path):
    import modules.leaps.service as service
    refs=[]
    pages=[]
    for strike in range(15,19):
        ref=raw_contract(strike=strike)
        raw=chain_raw()
        raw['details'].update(ticker=ref['ticker'],strike_price=strike)
        refs.append(ref)
        pages.append([raw])
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport(references=refs,chain_pages=pages))
    original=service.compare_contract
    evaluations=[]
    def counted(*args,**kwargs):
        evaluations.append(args[0].id)
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'compare_contract',counted)
    snapshot=finish(runner)
    assert len(evaluations)==4, 'unchanged prior contracts must not rerun six-scenario math on each page'
    assert snapshot.coverage['eligible']==4
    runner.provider.market.clock_ns=lambda:NOW+61*10**9
    expired=service.discover(runner.provider,run(),snapshot.cursor)
    assert len(evaluations)==4
    assert expired.coverage['eligible']==0
    assert all(row['assessment'].algebra_eligible and not row['assessment'].comparison_eligible for row in expired.data['comparisons'])
    assert all(row['observation'].quote.source_ns==NOW-1 for row in expired.data['comparisons'])


def test_expired_api_comparison_cannot_regain_freshness_when_wallclock_regresses(monkeypatch,tmp_path):
    runner,service=flow(monkeypatch,tmp_path,WholeFlowTransport())
    finish(runner)
    clock=[NOW]
    service.clock_ns=lambda:clock[0]
    first=request('/api/leaps/results').json()
    assert first['rows'][0]['assessment']['comparison_eligible'] is True
    clock[0]=NOW+61*10**9
    old=request('/api/leaps/results').json()
    assert old['rows'][0]['assessment']['comparison_eligible'] is False
    clock[0]=NOW+5*10**9
    after=request('/api/leaps/results')
    assert after.status_code==200
    assert after.json()['rows'][0]['assessment']['comparison_eligible'] is False
    assert 'clock_regression' in after.json()['rows'][0]['assessment']['reasons']
    fresh_store=LeapsStore(tmp_path,write_json_atomic,clock_ns=lambda:NOW)
    fresh_service=LeapsApiService(fresh_store,clock_ns=lambda:clock[0])
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:fresh_service)
    assert request('/api/leaps/results').json()['rows'][0]['assessment']['comparison_eligible'] is False
    scenario=request('/api/leaps/scenarios',method='POST',body=scenario_body(after.json(),terminal_prices=['25'])).json()
    assert scenario['assessment']['algebra_eligible'] and not scenario['assessment']['comparison_eligible']
    assert scenario['quote_source_ns']==str(NOW-1) and D(scenario['scenarios'][0]['result']['debit'])==1560
    saved=json.loads(runner.store.path.read_text())['clock_watermark_ns']
    runner.pause(run().run_id)
    runner.store.publish(replace(runner.store.load_complete(),status='paused'))
    assert json.loads(runner.store.path.read_text())['clock_watermark_ns']==saved==str(NOW+61*10**9)
    ctx=multiprocessing.get_context('spawn')
    parent,child=ctx.Pipe()
    process=ctx.Process(target=_clock_child,args=(str(tmp_path),child))
    process.start()
    try:
        assert parent.poll(20)
        assert parent.recv()==(False,True,str(NOW-1))
        process.join(5)
        assert process.exitcode==0
    finally:
        if process.is_alive():
            process.terminate(); process.join(5)
        parent.close(); child.close()


def _clock_child(root,pipe):
    store=LeapsStore(root,write_json_atomic,clock_ns=lambda:NOW)
    payload=LeapsApiService(store,clock_ns=lambda:NOW+5*10**9).results()
    row=payload['rows'][0]
    pipe.send((row['assessment']['comparison_eligible'],row['assessment']['algebra_eligible'],row['observation']['quote']['source_ns']))
    pipe.close()


@pytest.mark.parametrize('option_age,stock_age',[(20,0),(0,20),(20,30)])
def test_first_read_clock_regression_denies_current_both_sources_and_keeps_algebra(monkeypatch,tmp_path,option_age,stock_age):
    raw=chain_raw()
    raw['last_quote']['last_updated']=NOW-option_age*10**9-1
    stock=dict(T='SOFI',p=20,P=21,s=200,S=300,y=NOW-stock_age*10**9-1)
    runner,service=flow(monkeypatch,tmp_path,WholeFlowTransport(chain_pages=[[raw]],stock=stock))
    snapshot=finish(runner)
    assert snapshot.data['comparisons'][0]['assessment'].comparison_eligible
    assert json.loads(runner.store.path.read_text())['clock_watermark_ns']==str(NOW)
    service.clock_ns=lambda:NOW-10*10**9
    response=request('/api/leaps/results')
    assert response.status_code==200
    payload=response.json()
    row=payload['rows'][0]
    assert not row['assessment']['comparison_eligible'] and row['assessment']['algebra_eligible']
    assert 'clock_regression' in row['assessment']['reasons']
    for label,age in (('option',option_age),('stock',stock_age)):
        assert (label+'_future_source' in row['assessment']['reasons'])==(age<10)
    assert row['observation']['quote']['source_ns']==str(raw['last_quote']['last_updated'])
    assert row['stock_reference']['source_ns']==str(stock['y'])
    scenario=request('/api/leaps/scenarios',method='POST',body=scenario_body(payload,terminal_prices=['25']))
    assert scenario.status_code==200
    result=scenario.json()
    assert not result['assessment']['comparison_eligible'] and result['assessment']['algebra_eligible']
    assert result['quote_source_ns']==str(raw['last_quote']['last_updated'])
    assert D(result['scenarios'][0]['result']['debit'])==1560
    service.clock_ns=lambda:NOW+1
    assert request('/api/leaps/results').json()['rows'][0]['assessment']['comparison_eligible']


@pytest.mark.parametrize('route',['results','scenarios'])
def test_monotone_clock_out_of_order_read_arrivals_are_not_regression(monkeypatch,tmp_path,route):
    runner,service=flow(monkeypatch,tmp_path,WholeFlowTransport())
    finish(runner)
    initial=request('/api/leaps/results').json()
    entered,release=threading.Event(),threading.Event()
    clock=[NOW+10**9]
    service.clock_ns=lambda:clock[0]
    original=runner.store.observe_clock
    def delayed(clock_ns):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        return original(clock_ns)
    monkeypatch.setattr(runner.store,'observe_clock',delayed)
    replies=[]
    def first():
        replies.append(request('/api/leaps/results') if route=='results' else
                       request('/api/leaps/scenarios',method='POST',body=scenario_body(initial,terminal_prices=['25'])))
    thread=threading.Thread(target=first)
    thread.start()
    try:
        assert entered.wait(2)
        clock[0]=NOW+2*10**9
        assert request('/api/leaps/results').json()['rows'][0]['assessment']['comparison_eligible']
    finally:
        release.set()
        thread.join(5)
    assert replies[0].status_code==200
    payload=replies[0].json()
    assessment=payload['rows'][0]['assessment'] if route=='results' else payload['assessment']
    assert assessment['comparison_eligible'], assessment
    assert 'clock_regression' not in assessment['reasons']


@pytest.mark.parametrize('value',[None,'not-a-clock',-1])
def test_missing_or_invalid_v2_clock_watermark_fails_closed(monkeypatch,tmp_path,value):
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    finish(runner)
    raw=json.loads(runner.store.path.read_text())
    if value is None:
        raw.pop('clock_watermark_ns')
    else:
        raw['clock_watermark_ns']=value
    write_json_atomic(runner.store.path,raw)
    assert request('/api/leaps/results').status_code==503


def test_uncommitted_partial_blob_and_metadata_failure_never_replace_complete(monkeypatch,tmp_path):
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    final=finish(runner)
    before=runner.store.path.read_bytes()
    (runner.store.blobs/'.pending-synthetic-crash.json').write_bytes(b'{')
    writer=runner.store.atomic_writer
    def fail_metadata(path,payload):
        if Path(path)==runner.store.path:
            raise OSError('synthetic metadata commit failure')
        writer(path,payload)
    runner.store.atomic_writer=fail_metadata
    with pytest.raises(OSError):
        runner.store.publish(replace(final,run=replace(run(),run_id='uncommitted-B')))
    assert runner.store.path.read_bytes()==before
    runner.store.atomic_writer=writer
    assert request('/api/leaps/results').json()['run_id']==run().run_id
    assert LeapsStore(tmp_path,write_json_atomic,clock_ns=lambda:NOW).load_complete()==final


def test_reader_keeps_old_immutable_handle_across_atomic_complete_replacement(monkeypatch,tmp_path):
    import modules.leaps.runtime as runtime
    runner,_=flow(monkeypatch,tmp_path,WholeFlowTransport())
    final=finish(runner)
    entered,release=threading.Event(),threading.Event()
    decode=runtime._decode
    def slow(value):
        if isinstance(value,dict) and value.get('t')=='LeapsSnapshot' and not entered.is_set():
            entered.set()
            assert release.wait(5)
        return decode(value)
    monkeypatch.setattr(runtime,'_decode',slow)
    results=[]
    reader=threading.Thread(target=lambda:results.append(request('/api/leaps/results')))
    reader.start()
    try:
        assert entered.wait(2)
        runner.store.publish(replace(final,run=replace(run(),run_id='completed-B')))
        assert request('/api/leaps/results').json()['run_id']=='completed-B'
    finally:
        release.set()
        reader.join(5)
    assert results[0].status_code==200 and results[0].json()['run_id']==run().run_id
    assert request('/api/leaps/scenarios',method='POST',body=scenario_body(results[0].json())).status_code==409
