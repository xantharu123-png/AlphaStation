"""Actual authenticated ASGI boundaries plus deterministic local policy limits."""
import threading
import asyncio
import pytest
import api
from test_leaps_api import auth, enabled, request
from modules.leaps import api_contract


def limiter(**kwargs):
    assert hasattr(api_contract,'LeapsReadAdmission'), 'LEAPS read admission is absent'
    return api_contract.LeapsReadAdmission(**kwargs)


def test_rapid_authorized_reads_have_retry_after_and_separate_light_budget(monkeypatch,tmp_path):
    enabled(monkeypatch,tmp_path)
    responses=[request('/api/leaps/results') for _ in range(7)]
    assert [r.status_code for r in responses]==[200]*6+[429]
    assert 1<=int(responses[-1].headers['retry-after'])<=60
    assert request('/api/leaps/status').status_code==200
    assert request('/api/leaps/capabilities').status_code==200
    assert request('/api/leaps/results',token='customer').status_code==200


def test_rolling_limits_have_bounded_account_memory_and_true_time(monkeypatch):
    clock=[100.0]
    gate=limiter(clock=lambda:clock[0],max_accounts=2)
    for group in range(5):
        for _ in range(6):
            gate.claim('a','results').release()
        clock[0]+=1.1
    with pytest.raises(api_contract.LeapsApiError) as full:
        gate.claim('a','scenarios')
    assert full.value.status==429 and full.value.retry_after==55
    gate.claim('b','results').release()
    with pytest.raises(api_contract.LeapsApiError) as capacity:
        gate.claim('c','results')
    assert capacity.value.status==429
    clock[0]+=61
    gate.claim('c','results').release()
    assert len(gate._accounts)<=2
    for group in range(6):
        for _ in range(20):
            gate.claim('c','status').release()
        clock[0]+=1.1
    with pytest.raises(api_contract.LeapsApiError) as light:
        gate.claim('c','capabilities')
    assert light.value.status==429
    gate.claim('c','results').release()


def test_two_physical_heavy_readers_and_error_release(monkeypatch,tmp_path):
    _,backend,_=enabled(monkeypatch,tmp_path)
    entered=threading.Barrier(3)
    release=threading.Event()
    responses=[]
    def blocked(**kwargs):
        entered.wait(timeout=10)
        assert release.wait(10)
        raise RuntimeError('synthetic failure')
    monkeypatch.setattr(backend,'results',blocked)
    threads=[threading.Thread(target=lambda:responses.append(request('/api/leaps/results'))) for _ in range(2)]
    for worker in threads: worker.start()
    try:
        entered.wait(timeout=10)
        denial=request('/api/leaps/results',token='customer')
        assert denial.status_code==429
        assert int(denial.headers['retry-after'])>=1
        assert request('/api/leaps/status').status_code==200
    finally:
        release.set()
        for worker in threads: worker.join(10)
    assert all(r.status_code==503 for r in responses)
    monkeypatch.setattr(backend,'results',lambda **kwargs:{'ok':True})
    assert request('/api/leaps/results').status_code==200


def test_permission_origin_and_body_caps_precede_expensive_work(monkeypatch,tmp_path):
    _,backend,_=enabled(monkeypatch,tmp_path)
    monkeypatch.setattr(backend,'scenarios',lambda *args:pytest.fail('body cannot reach store'))
    large=b'{"padding":"'+b'x'*16384+b'"}'
    denied=request('/api/leaps/scenarios',method='POST',token=None,raw_body=large)
    assert denied.status_code==401
    origin=request('/api/leaps/scenarios',method='POST',headers={'Origin':'https://untrusted.invalid'},raw_body=large)
    assert origin.status_code==403
    oversized=request('/api/leaps/scenarios',method='POST',raw_body=large)
    assert oversized.status_code==413
    assert oversized.json()['detail']['reason']=='body_too_large'
    # Malformed parse releases its heavy slot, too.
    for _ in range(2):
        assert request('/api/leaps/scenarios',method='POST',raw_body=b'{').status_code==422
    assert request('/api/leaps/results').status_code==200


def test_client_cancellation_retains_heavy_slot_until_processing_exits(monkeypatch,tmp_path):
    from starlette.requests import Request
    _,backend,_=enabled(monkeypatch,tmp_path)
    entered=threading.Event();release=threading.Event()
    def blocked(**kwargs):
        entered.set();assert release.wait(10);return {'ok':True}
    monkeypatch.setattr(backend,'results',blocked)
    async def check():
        req=Request({'type':'http','method':'GET','scheme':'http','path':'/api/leaps/results','query_string':b'',
            'headers':[(b'authorization',b'Bearer admin')],'server':('testserver',80)})
        task=asyncio.create_task(api.api_leaps_results(req,limit=20,offset=0,view='complete',run_id=None,sort_by='be_rise',order='asc'))
        assert await asyncio.to_thread(entered.wait,5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
        assert api._LEAPS_READ_ADMISSION._heavy==1
        second=api._LEAPS_READ_ADMISSION.claim('another-account','results')
        with pytest.raises(api_contract.LeapsApiError,match='heavy_read_busy'):
            api._LEAPS_READ_ADMISSION.claim('third-account','results')
        second.release();release.set()
        for _ in range(100):
            if api._LEAPS_READ_ADMISSION._heavy==0:break
            await asyncio.sleep(.01)
        assert api._LEAPS_READ_ADMISSION._heavy==0
    try: asyncio.run(check())
    finally: release.set()


def test_chunked_body_stops_at_cap_before_more_chunks_or_json_decode():
    from starlette.requests import Request
    from fastapi import HTTPException
    async def check():
        parts=[b' '*16384,b'x',b'never-consume']
        consumed=[]
        async def receive():
            value=parts.pop(0);consumed.append(value)
            return {'type':'http.request','body':value,'more_body':True}
        req=Request({'type':'http','headers':[]},receive)
        with pytest.raises(HTTPException) as error:await api._leaps_body(req)
        assert error.value.status_code==413 and len(consumed)==2
    asyncio.run(check())


def test_heavy_admission_covers_actual_response_serialization(monkeypatch,tmp_path):
    _,backend,_=enabled(monkeypatch,tmp_path)
    held=[]
    class ObservedDict(dict):
        def items(self):
            held.append(api._LEAPS_READ_ADMISSION._heavy)
            return super().items()
    monkeypatch.setattr(backend,'results',lambda **kwargs:ObservedDict(ok=True))
    assert request('/api/leaps/results').status_code==200
    assert held and all(value==1 for value in held)
    assert api._LEAPS_READ_ADMISSION._heavy==0
