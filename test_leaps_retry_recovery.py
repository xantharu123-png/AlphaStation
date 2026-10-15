"""Offline recovery regressions: transport/wall-clock wait only are synthetic."""
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal
from email.utils import format_datetime
import threading

import pytest

from modules.leaps.api_contract import LeapsApiService
from modules.leaps.models import LeapsDataError
from modules.leaps.provider import MassiveLeapsProvider, probe_capabilities
from tests.fixtures.leaps.provider import Admission, NOW, Response, Transport, capability, run
from test_leaps_runtime import make_runner


def limited_response(retry_after=None):
    response = Response(status=429)
    response.headers = {} if retry_after is None else {'Retry-After': retry_after}
    return response


def provider_for(*responses):
    clock = [NOW]
    admission = Admission()
    transport = Transport(responses, admission)
    provider = MassiveLeapsProvider(transport, lambda: clock[0], admission, capability())
    return provider, transport, admission, clock


@pytest.mark.parametrize('header', ['7', format_datetime(datetime.fromtimestamp(NOW // 10**9 + 7, timezone.utc), usegmt=True)])
def test_retry_after_blocks_early_request_then_recovers_without_reconciliation(header):
    provider, transport, admission, clock = provider_for(limited_response(header), Response())
    with pytest.raises(LeapsDataError, match='rate_limited') as caught:
        provider.list_contract_page(run())
    assert getattr(caught.value, 'retry_at_ns', None) == NOW + 7 * 10**9
    clock[0] += 6 * 10**9
    with pytest.raises(LeapsDataError, match='rate_limited'):
        provider.list_contract_page(run())
    assert len(transport.calls) == 1 and not admission.held
    clock[0] += 10**9
    assert provider.list_contract_page(run()).complete
    assert len(transport.calls) == 2 and not admission.held


def test_invalid_retry_after_uses_bounded_exponential_backoff():
    provider, transport, _, clock = provider_for(*(limited_response('nonsense') for _ in range(5)), Response())
    for delay in (15, 30, 60, 120, 300):
        with pytest.raises(LeapsDataError, match='rate_limited') as caught:
            provider.list_contract_page(run())
        assert getattr(caught.value, 'retry_at_ns', None) == clock[0] + delay * 10**9
        clock[0] += delay * 10**9
    assert provider.list_contract_page(run()).complete
    assert len(transport.calls) == 6


def test_fifth_consecutive_rate_limit_saves_partial_cursor_and_stops_worker(tmp_path, monkeypatch):
    runner, transport, clock, _ = make_runner(tmp_path)
    original = transport.request
    attempts = []
    def request(method, url, **kwargs):
        if len(attempts) < 5:
            attempts.append(url)
            transport.calls.append(url)
            return limited_response()
        return original(method, url, **kwargs)
    transport.request = request
    service = LeapsApiService(runner.store, runner=runner, clock_ns=clock)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        clock.now += int(seconds * 10**9)
        refresh_priority(runner, clock)
        return False
    monkeypatch.setattr(service._wakeup, 'wait', wait)
    service.scan(run())
    service._worker.join(3)
    assert not service._worker.is_alive()
    partial = runner.snapshot()
    assert partial.status == 'partial' and partial.cursor is not None
    assert 'rate_limit_retry_exhausted' in partial.reasons
    assert partial.coverage['retry']['retry_at_ns'] == NOW + 525 * 10**9
    assert len(attempts) == 5 and waits == [15, 30, 60, 60, 60]
    clock.now += 300 * 10**9
    refresh_priority(runner, clock)
    assert runner.run_slice() == partial
    runner.resume(run().run_id)
    assert runner.run_slice().status == 'complete'


def test_temporary_admission_retains_reason_and_retry_deadline_without_transport():
    provider, transport, admission, _ = provider_for(Response())
    admission.allowed = False
    with pytest.raises(LeapsDataError, match='admission_denied') as caught:
        provider.list_contract_page(run())
    assert getattr(caught.value, 'retry_reason', None) == 'priority_active'
    assert getattr(caught.value, 'retry_at_ns', None) == NOW + 10 * 10**9
    assert transport.calls == [] and not admission.held


def refresh_priority(runner, clock):
    runner.admission.priority.publish_owners({'stock': False, 'bi': False, 'alert': False}, clock.now)


def fail_first_request(transport):
    original = transport.request
    first = True
    def request(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            transport.calls.append(args[1])
            return limited_response('2')
        return original(*args, **kwargs)
    transport.request = request


def test_runner_waits_then_automatically_recovers_rate_limit_on_original_run(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    fail_first_request(transport)
    runner.start(run())
    waiting = runner.run_slice()
    assert waiting.status == 'paused'
    assert waiting.coverage.get('retry', {}).get('reason') == 'rate_limited'
    assert runner.store.read(metadata=True)['control']['action'] == 'run'
    assert runner.run_slice() == waiting and len(transport.calls) == 1
    clock.now += 2 * 10**9
    refresh_priority(runner, clock)
    final = runner.run_slice()
    assert final.status == 'complete' and final.run == run()
    assert final.coverage['parsed'] == 1
    assert not runner.authority_states().get('market', {}).get('reason')
    assert 'retry' not in final.coverage


def test_worker_waits_on_priority_then_resumes_without_manual_control(tmp_path, monkeypatch):
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.admission.priority.publish_owners({'stock': True, 'bi': False, 'alert': False}, clock.now)
    service = LeapsApiService(runner.store, runner=runner, clock_ns=clock)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        clock.now += int(seconds * 10**9)
        refresh_priority(runner, clock)
        return False
    # Replace only elapsed-time waiting, leaving discovery/locks/admission real.
    wakeup = getattr(service, '_wakeup', threading.Event())
    monkeypatch.setattr(wakeup, 'wait', wait)
    service._wakeup = wakeup
    service.scan(run())
    service._worker.join(3)
    assert not service._worker.is_alive()
    assert runner.snapshot().status == 'complete'
    assert waits and all(0 < seconds <= 60 for seconds in waits)
    assert len(transport.calls) == 6


def test_api_worker_recovers_rate_limit_without_resume_request(tmp_path, monkeypatch):
    runner, transport, clock, _ = make_runner(tmp_path)
    fail_first_request(transport)
    service = LeapsApiService(runner.store, runner=runner, clock_ns=clock)
    waits = []
    def wait(seconds):
        waits.append(seconds)
        clock.now += int(seconds * 10**9)
        refresh_priority(runner, clock)
        return False
    monkeypatch.setattr(service._wakeup, 'wait', wait)
    service.scan(run())
    service._worker.join(3)
    assert not service._worker.is_alive()
    assert runner.snapshot().status == 'complete'
    assert service.status()['worker_error'] is None
    assert waits == [2]
    assert sum('/reference/options/contracts' in url for url in transport.calls) == 2


def test_manual_api_pause_wakes_retry_worker_and_does_not_retry(tmp_path, monkeypatch):
    runner, transport, clock, _ = make_runner(tmp_path)
    fail_first_request(transport)
    service = LeapsApiService(runner.store, runner=runner, clock_ns=clock)
    def wait(seconds):
        assert seconds == 2
        service.control(run().run_id, 'pause')
        clock.now += 3 * 10**9
        refresh_priority(runner, clock)
        return True
    monkeypatch.setattr(service._wakeup, 'wait', wait)
    service.scan(run())
    service._worker.join(3)
    assert not service._worker.is_alive()
    assert runner.snapshot().status == 'paused'
    assert 'admin_pause' in runner.snapshot().reasons
    assert len(transport.calls) == 1


def test_recreated_provider_keeps_persisted_wait_until_due(tmp_path):
    runner, transport, clock, admission = make_runner(tmp_path)
    fail_first_request(transport)
    runner.start(run())
    runner.run_slice()
    restored, new_transport, _, _ = make_runner(tmp_path, gate=admission, clock=clock)
    # Process recreation still needs worker authorization, not entitlement repair.
    assert restored.run_slice().status == 'paused' and new_transport.calls == []
    restored.resume(run().run_id)
    assert restored.run_slice().status == 'paused' and new_transport.calls == []
    clock.now += 2 * 10**9
    refresh_priority(restored, clock)
    assert restored.run_slice().status == 'complete'
    assert len(new_transport.calls) == 6


def test_manual_pause_during_automatic_wait_stays_manual(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    fail_first_request(transport)
    runner.start(run())
    runner.run_slice()
    runner.pause(run().run_id)
    clock.now += 3 * 10**9
    refresh_priority(runner, clock)
    paused = runner.run_slice()
    assert paused.status == 'paused' and 'admin_pause' in paused.reasons
    assert len(transport.calls) == 1


def test_existing_run_resumes_after_et_midnight_without_changing_valuation_date(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.start(run())
    runner.pause(run().run_id)
    clock.now += 86400 * 10**9
    assert LeapsApiService(runner.store, runner=runner, clock_ns=clock).status()['resumable'] is True
    assert runner.resume(run().run_id).run.market_date == date(2026, 10, 13)
    assert LeapsApiService(runner.store, runner=runner, clock_ns=clock).status()['market_date'] == '2026-10-13'
    assert transport.calls == []


def test_running_work_crosses_et_midnight_with_original_date_and_quote_clocks(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    midnight = int(datetime(2026, 10, 14, 4, tzinfo=timezone.utc).timestamp()) * 10**9
    clock.now = midnight - 1
    runner.admission.budget.proof = replace(runner.admission.budget.proof, valid_until_ns=midnight + 100 * 10**9)
    runner.admission.priority.proof = replace(runner.admission.priority.proof, valid_until_ns=midnight + 100 * 10**9)
    refresh_priority(runner, clock)
    runner.start(run())
    first = True
    def cross_midnight(_):
        nonlocal first
        if first:
            first = False
            clock.now = midnight
            refresh_priority(runner, clock)
    # Cross during the stock request; the next safe unit must retain the run date.
    original_request = transport.request
    def request(method, url, **kwargs):
        if '/last/nbbo/' in url:
            cross_midnight(url)
        return original_request(method, url, **kwargs)
    transport.request = request
    final = runner.run_slice()
    assert final.status == 'complete' and final.run.market_date == date(2026, 10, 13)
    assert final.data['comparisons'][0]['observation'].quote.source_ns == NOW - 1
    assert final.data['comparisons'][0]['stock_reference'].source_ns == NOW - 1


def test_future_run_date_cannot_start_or_resume(tmp_path):
    runner, transport, _, _ = make_runner(tmp_path)
    future = replace(run(), market_date=date(2026, 10, 14))
    with pytest.raises(LeapsDataError, match='stale_run'):
        runner.start(future)
    runner.start(run())
    runner.pause(run().run_id)
    saved = runner.snapshot()
    runner.store.publish(replace(saved, run=future))
    with pytest.raises(LeapsDataError, match='stale_run'):
        runner.resume(future.run_id)
    assert transport.calls == []


def test_sec_rate_limit_retries_saved_underlying_without_refetching_stock_or_history(tmp_path):
    from tests.fixtures.leaps.discovery import completion
    runner, transport, clock, _ = make_runner(tmp_path, sec=True, completion_producer=completion)
    original = transport.request
    sec_attempts = []
    def request(method, url, **kwargs):
        if 'sec.gov' in url:
            sec_attempts.append(url)
            if len(sec_attempts) == 1:
                transport.calls.append(url)
                return limited_response('2')
        return original(method, url, **kwargs)
    transport.request = request
    runner.start(run())
    waiting = runner.run_slice()
    assert waiting.status == 'paused'
    assert waiting.data['pending_underlyings']['SOFI']['reason'] == 'rate_limited'
    clock.now += 2 * 10**9
    refresh_priority(runner, clock)
    final = runner.run_slice()
    assert final.status == 'complete'
    assert len(sec_attempts) == 2
    assert sum('/last/nbbo/' in url for url in transport.calls) == 1
    assert sum('/v2/aggs/' in url for url in transport.calls) == 1
    assert final.data['comparisons'][0]['stock_reference'].source_ns == NOW - 1


def test_probe_calendar_uses_et_instead_of_utc_date():
    provider, transport, _, clock = provider_for(Response(), Response(), Response())
    clock[0] = int(datetime(2026, 10, 14, 1, tzinfo=timezone.utc).timestamp()) * 10**9
    probe_capabilities(provider, max_requests=2)
    assert 'expiration_date.gte=2027-10-13' in transport.calls[1][1]


def test_clock_rollback_persists_failed_checkpoint_and_releases_worker_owner(tmp_path):
    from modules.leaps.admission import OSLease
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.start(run())
    last_complete = runner.run_slice()
    assert last_complete.status == 'complete'
    transport.calls.clear()
    service = LeapsApiService(runner.store, runner=runner, clock_ns=clock)
    def rollback(_):
        clock.now -= 1
    transport.before = rollback
    service.scan(replace(run(), run_id='clock-rollback'))
    service._worker.join(3)
    assert not service._worker.is_alive()
    saved = runner.snapshot()
    assert saved.status == 'failed'
    assert 'clock_regression' in saved.reasons
    assert saved.cursor is not None and saved.coverage['reference_seen'] == 1
    assert runner.store.read(metadata=True)['control']['action'] == 'pause'
    assert runner.store.load_complete() == last_complete
    assert service.status()['restartable'] is True
    assert len(transport.calls) == 1
    owner = OSLease(runner.store.root / 'worker.lock')
    assert owner.acquire()
    owner.release()
