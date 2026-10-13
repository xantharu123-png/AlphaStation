"""Offline runtime contracts; subprocesses exercise real OS ownership."""
from dataclasses import replace
from datetime import date
from decimal import Decimal
from importlib import import_module
from pathlib import Path
import json
import multiprocessing
import threading

import pytest

from modules.atomic_cache import write_json_atomic
from modules.leaps.models import LeapsSnapshot, LeapsDataError
from tests.fixtures.leaps.provider import NOW, run


def runtime():
    assert Path('modules/leaps/runtime.py').is_file(), 'persistent LEAPS runtime missing'
    return import_module('modules.leaps.runtime')


def admission_module():
    assert Path('modules/leaps/admission.py').is_file(), 'bounded LEAPS admission missing'
    return import_module('modules.leaps.admission')


def snap(status='complete', **changes):
    return replace(LeapsSnapshot(run(), status, {'price': Decimal('1.2300')},
                                {'parsed': 1}, (), None, ()), **changes)


def test_store_fresh_read_has_no_run_or_provider_io(tmp_path):
    store = runtime().LeapsStore(tmp_path, write_json_atomic)
    assert store.load() is None
    assert store.load_complete() is None
    assert all(p.parent == tmp_path / 'leaps' for p in tmp_path.rglob('*') if p.is_file())


def test_store_roundtrips_exact_money_and_preserves_complete_separately(tmp_path):
    store = runtime().LeapsStore(tmp_path, write_json_atomic)
    final = snap()
    store.publish(final)
    part = snap('paused', coverage={'parsed': 2}, reasons=('admin_pause',))
    store.publish(part)
    reopened = runtime().LeapsStore(tmp_path, write_json_atomic)
    assert reopened.load() == part
    assert reopened.load_complete() == final
    assert str(reopened.load().data['price']) == '1.2300'


def test_failed_atomic_publication_retains_previous_complete(tmp_path):
    store = runtime().LeapsStore(tmp_path, write_json_atomic)
    store.publish(snap())
    def fail(path, payload):
        raise OSError('synthetic disk failure')
    broken = runtime().LeapsStore(tmp_path, fail)
    with pytest.raises(OSError):
        broken.publish(snap('running'))
    assert store.load() == store.load_complete() == snap()


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), Decimal('NaN'), object(), {1: 'x'}])
def test_store_rejects_nonfinite_or_unsafe_data_before_writer(tmp_path, bad):
    writes = []
    def writer(path, payload):
        writes.append(path)
        write_json_atomic(path, payload)
    store = runtime().LeapsStore(tmp_path, writer)
    writes.clear()
    with pytest.raises((LeapsDataError, ValueError, TypeError)):
        store.publish(snap(data={'bad': bad}))
    assert not writes


def test_store_rejects_corrupt_schema_without_overwriting(tmp_path):
    store = runtime().LeapsStore(tmp_path, write_json_atomic)
    store.publish(snap())
    path = tmp_path / 'leaps' / 'state.json'
    raw = json.loads(path.read_text())
    raw['unexpected'] = 'bad'
    write_json_atomic(path, raw)
    before = path.read_bytes()
    with pytest.raises(LeapsDataError):
        store.publish(snap('running'))
    assert path.read_bytes() == before


def test_real_lease_cannot_be_released_by_other_thread(tmp_path):
    a = admission_module()
    folder = a.private_namespace(tmp_path)
    lease = a.OSLease(folder / 'request.lock')
    assert lease.acquire()
    errors = []
    def release_elsewhere():
        try:
            lease.release()
        except RuntimeError:
            errors.append('not_owner')
    thread = threading.Thread(target=release_elsewhere)
    thread.start(); thread.join()
    contender = a.OSLease(folder / 'request.lock')
    assert errors == ['not_owner'] and not contender.acquire()
    lease.release()
    assert contender.acquire()
    contender.release()


def _lease_child(path, conn):
    from modules.leaps.admission import OSLease
    lease = OSLease(Path(path))
    acquired = lease.acquire()
    conn.send(acquired)
    if acquired:
        conn.recv()
        lease.release()
    conn.close()


def test_request_lease_is_cross_process_and_crash_releases_it(tmp_path):
    a = admission_module()
    path = a.private_namespace(tmp_path) / 'request.lock'
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_lease_child, args=(str(path), child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() is True
        contender = a.OSLease(path)
        assert not contender.acquire()
        process.terminate(); process.join(10)
        assert not process.is_alive()
        assert contender.acquire()
        contender.release()
    finally:
        if process.is_alive():
            process.terminate(); process.join(10)
        parent.close(); child.close()


class Clock:
    def __init__(self):
        self.now = NOW
    def __call__(self):
        return self.now


def certified(tmp_path, allocation=200, clock=None):
    a = admission_module()
    clock = clock or Clock()
    folder = a.private_namespace(tmp_path)
    lock = a.OSLease(folder / 'synthetic-shared.lock')
    assert lock.acquire(); lock.release()
    write_json_atomic(folder / 'synthetic-shared.json', {'window': NOW // (60 * 10**9), 'count': 0})
    priority_lock = a.OSLease(folder / 'priority.lock')
    assert priority_lock.acquire(); priority_lock.release()
    write_json_atomic(folder / 'priority.json', {'observed_ns': NOW, 'owners': {'stock': False, 'bi': False, 'alert': False}})
    proof = a.BudgetProof(allocation, NOW - 1, NOW + 10**12, ('synthetic-allocation',),
                          a.lock_identity(folder / 'synthetic-shared.lock'), str((folder / 'synthetic-shared.json').resolve()))
    budget = a.FileBudget(tmp_path, folder / 'synthetic-shared.lock', folder / 'synthetic-shared.json', proof)
    priority = a.PriorityGate(tmp_path, a.PriorityProof(
        NOW - 1, NOW + 10**12, ('synthetic-complete-topology',), ('stock', 'bi', 'alert'),
        a.lock_identity(folder / 'priority.lock')))
    a.Admission.provision(tmp_path, write_json_atomic)
    admission = a.Admission(budget, priority, clock, atomic_writer=write_json_atomic)
    return admission, clock, budget, priority


@pytest.mark.parametrize('allocation,cap', [(200, 20), (50, 5), (9, 0)])
def test_local_cap_both_scopes_market_token_only(tmp_path, allocation, cap):
    gate, clock, budget, priority = certified(tmp_path, allocation)
    for i in range(cap):
        scope = 'market' if i % 2 == 0 else 'sec'
        assert gate.try_acquire(scope).allowed
        gate.release(scope)
    assert gate.try_acquire('sec').allowed is False
    shared = json.loads(budget.state_path.read_text())
    assert shared['count'] == (cap + 1) // 2
    if cap:
        clock.now += 60 * 10**9
        priority.publish_owners({'stock': False, 'bi': False, 'alert': False}, clock.now)
        assert gate.try_acquire('sec').allowed
        gate.release('sec')


@pytest.mark.parametrize('allocation', [None, 0, -1, True, '200', 20.5])
def test_unknown_invalid_zero_budget_has_no_provider_io(tmp_path, allocation):
    from modules.leaps.provider import MassiveLeapsProvider
    from tests.fixtures.leaps.provider import capability
    gate, clock, _, _ = certified(tmp_path, allocation)
    class NoIO:
        def market_headers(self):
            pytest.fail('no headers/transport permitted')
    provider = MassiveLeapsProvider(NoIO(), clock, gate, capability())
    with pytest.raises(LeapsDataError, match='admission_denied'):
        provider.list_contract_page(run())
    assert provider.request_count == 0


def test_unknown_priority_default_deny_without_io(tmp_path):
    a = admission_module()
    assert not a.Admission(None, None, Clock()).try_acquire('market').allowed
    gate, clock, budget, _ = certified(tmp_path)
    denied = a.Admission(budget, None, clock, atomic_writer=write_json_atomic)
    assert not denied.try_acquire('market').allowed
    assert json.loads(budget.state_path.read_text())['count'] == 0


def test_priority_starts_during_inflight_and_next_ticket_waits(tmp_path):
    gate, clock, budget, priority = certified(tmp_path)
    assert gate.try_acquire('market').allowed
    # Request lease stays held, but shared token and priority locks are free.
    contender = admission_module().OSLease(budget.lock_path, private=False)
    assert contender.acquire(); contender.release()
    priority.publish_owners({'stock': False, 'bi': True, 'alert': False}, clock.now)
    assert not gate.try_acquire('sec').allowed
    gate.release('market')
    assert gate.try_acquire('market').reason == 'priority_active'
    assert json.loads(budget.state_path.read_text())['count'] == 1


@pytest.mark.parametrize('target', ['shared', 'usage', 'priority'])
def test_corrupt_state_denies_without_reset(tmp_path, target):
    gate, _, budget, priority = certified(tmp_path)
    path = {'shared': budget.state_path, 'usage': gate.root / 'usage.json',
            'priority': priority.state_path}[target]
    write_json_atomic(path, {'corrupt': True})
    before = path.read_bytes()
    assert not gate.try_acquire('market').allowed
    assert path.read_bytes() == before


def test_stale_or_incomplete_priority_proof_denies(tmp_path):
    gate, clock, budget, priority = certified(tmp_path)
    clock.now += 6 * 10**9
    assert not gate.try_acquire('sec').allowed
    priority.publish_owners({'stock': False, 'bi': False, 'alert': False}, clock.now)
    assert gate.try_acquire('sec').allowed
    gate.release('sec')
    priority.proof = replace(priority.proof, owner_keys=('stock', 'bi'))
    assert not gate.try_acquire('market').allowed
    assert json.loads(budget.state_path.read_text())['count'] == 0


class RoutedTransport:
    """Synthetic authentic provider responses, only transport is replaced."""
    def __init__(self, clock):
        self.clock, self.calls = clock, []
        self.before = None
        self.sec_status = 200
        self.market_status = 200

    def market_headers(self):
        return {}

    def request(self, method, url, **kwargs):
        from urllib.parse import urlsplit, parse_qs
        from tests.fixtures.leaps.provider import Response, raw_contract, listing, chain_raw
        self.calls.append(url)
        if self.before:
            self.before(url)
        path = urlsplit(url).path
        if 'sec.gov' in url:
            return Response({'cik': 1, 'facts': {}}, status=self.sec_status)
        if self.market_status != 200:
            return Response(status=self.market_status)
        if path == '/v3/reference/options/contracts':
            items = [raw_contract()]
        elif path == '/v3/reference/tickers':
            items = [listing(cik='1')] if parse_qs(urlsplit(url).query)['type'] == ['CS'] else []
        elif path == '/v2/last/nbbo/SOFI':
            return Response({'status': 'OK', 'results': {'T': 'SOFI', 'p': 20, 'P': 21,
                                                        's': 10, 'S': 20, 'y': NOW - 1}})
        elif path == '/v3/snapshot/options/SOFI':
            items = [chain_raw()]
        elif path.startswith('/v2/aggs/ticker/SOFI/'):
            return Response({'status': 'OK', 'ticker': 'SOFI', 'results': [
                {'t': 1791777600000, 'o': 19, 'h': 21, 'l': 18, 'c': 20, 'v': 12345}]})
        else:
            raise AssertionError('unexpected synthetic route')
        return Response({'status': 'OK', 'results': items})


def make_runner(tmp_path, *, gate=None, clock=None, store=None, sec=False, completion_producer=None):
    from modules.leaps.provider import MassiveLeapsProvider
    from modules.leaps.service import DiscoveryProvider
    from modules.leaps.underlying import CachedUnderlyingLoader
    from tests.fixtures.leaps.provider import capability
    from tests.fixtures.leaps.discovery import term_producer, session
    if gate is None:
        gate, clock, _, _ = certified(tmp_path, clock=clock)
    transport = RoutedTransport(clock)
    provider = MassiveLeapsProvider(transport, clock, gate, capability())
    wrapper = DiscoveryProvider(provider, terms_producer=term_producer, session_producer=session,
                                underlying_loader=CachedUnderlyingLoader(provider, completion_producer=completion_producer,
                                    configured_sec_user_agent='Synthetic qa@example.invalid' if sec else None))
    store = store or runtime().LeapsStore(tmp_path, write_json_atomic)
    return runtime().LeapsRunner(wrapper, store, gate, clock), transport, clock, gate


def test_start_ack_no_io_then_worker_completes_with_exact_checkpoint(tmp_path):
    runner, transport, _, _ = make_runner(tmp_path)
    assert runner.snapshot() is None
    ack = runner.start(run())
    assert ack.status == 'queued' and transport.calls == []
    final = runner.run_slice()
    assert final.status == 'complete'
    assert final.coverage['parsed'] == 1
    assert json.loads(final.cursor)['version'] == 2
    assert final.coverage['progress']['completed'] == final.coverage['progress']['total'] == 1
    assert runner.store.load_complete() == final


def test_pause_ack_during_request_checkpoints_before_next_unit(tmp_path):
    runner, transport, _, _ = make_runner(tmp_path)
    runner.start(run())
    transport.before = lambda _: runner.pause(run().run_id)
    partial = runner.run_slice()
    assert partial.status == 'paused'
    assert partial.coverage['reference_seen'] == 1
    assert len(transport.calls) == 1
    assert runner.run_slice() == partial
    transport.before = None
    runner.resume(run().run_id)
    final = runner.run_slice()
    assert final.status == 'complete' and final.coverage['parsed'] == 1
    assert sum('/reference/options/contracts' in x for x in transport.calls) == 1


def test_restart_does_not_resume_until_explicit_control(tmp_path):
    runner, transport, clock, gate = make_runner(tmp_path)
    runner.start(run())
    transport.before = lambda _: runner.pause(run().run_id)
    paused = runner.run_slice()
    other, calls, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    assert other.snapshot() == paused and other.run_slice() == paused
    assert calls.calls == []
    other.resume(run().run_id)
    assert other.run_slice().status == 'complete'
    assert not any('/reference/options/contracts' in x for x in calls.calls)


def test_deadline_yields_resumable_partial_not_complete_zero(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.start(run())
    def advance(_):
        clock.now += 901 * 10**9
    transport.before = advance
    part = runner.run_slice()
    assert part.status == 'partial' and 'slice_deadline' in part.reasons
    assert part.coverage['reference_seen'] == 1 and part.cursor
    assert runner.store.load_complete() is None
    assert len(transport.calls) == 1


@pytest.mark.parametrize('changes', [dict(market_date=date(2026, 10, 14)), dict(filters={'max_spread': Decimal('.2')}),
                                      dict(universe_revision='v2'), dict(schema_version='2'), dict(methodology_version='2')])
def test_same_run_changed_binding_cannot_reuse_cursor(tmp_path, changes):
    runner, transport, _, _ = make_runner(tmp_path)
    runner.start(run())
    runner.pause(run().run_id)
    with pytest.raises(LeapsDataError):
        runner.start(replace(run(), **changes))
    assert transport.calls == []


def test_explicit_new_run_discards_old_cursor_not_complete_history(tmp_path):
    runner, _, _, _ = make_runner(tmp_path)
    runner.start(run())
    final = runner.run_slice()
    new = replace(run(), run_id='new', filters={'max_spread': Decimal('.2')})
    ack = runner.start(new)
    assert ack.run == new and ack.cursor is None and ack.status == 'queued'
    assert runner.store.load_complete() == final


def test_stale_date_resume_and_bad_control_id_no_io(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.start(run()); runner.pause(run().run_id)
    with pytest.raises(LeapsDataError):
        runner.resume('different')
    clock.now += 86400 * 10**9
    with pytest.raises(LeapsDataError, match='stale_run'):
        runner.resume(run().run_id)
    assert transport.calls == []


def test_unknown_admission_runner_does_no_io(tmp_path):
    clock = Clock()
    gate = admission_module().Admission(None, None, clock)
    runner, transport, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    runner.start(run())
    result = runner.run_slice()
    assert result.status == 'paused' and 'admission_denied' in result.reasons
    assert transport.calls == []


@pytest.mark.parametrize('status,reason', [(401, 'authorization_denied'), (429, 'rate_limited')])
def test_sec_failure_is_persisted_and_cannot_poison_market_on_new_provider(tmp_path, status, reason):
    runner, transport, clock, gate = make_runner(tmp_path, sec=True)
    transport.sec_status = status
    runner.start(run())
    final = runner.run_slice()
    assert final.status == 'complete' and final.coverage['parsed'] == 1
    assert runner.authority_states()['sec']['reason'] == reason
    assert 'market' not in runner.authority_states()
    newer, transport2, _, _ = make_runner(tmp_path, gate=gate, clock=clock, sec=True)
    newer.start(replace(run(), run_id='new'))
    result = newer.run_slice()
    assert result.status == 'complete' and result.coverage['parsed'] == 1
    assert not any('sec.gov' in x for x in transport2.calls)
    assert any('/snapshot/options/' in x for x in transport2.calls)


def test_market_authority_failure_stays_blocked_new_run_and_reconcile_defaults_denied(tmp_path):
    runner, transport, clock, gate = make_runner(tmp_path)
    transport.market_status = 401
    runner.start(run())
    assert runner.run_slice().status == 'failed'
    with pytest.raises(LeapsDataError, match='terminal_run'):
        runner.resume(run().run_id)
    other, transport2, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    other.start(replace(run(), run_id='new'))
    assert other.run_slice().status == 'failed'
    assert transport2.calls == []
    with pytest.raises(LeapsDataError):
        other.reconcile_authority('market', None, None)


def test_snapshot_publication_identity_changes_not_original_quote_clock(tmp_path):
    runner, _, _, _ = make_runner(tmp_path)
    runner.start(run())
    first = runner.store.identity()
    result = runner.run_slice()
    second = runner.store.identity()
    assert first['run_id'] == second['run_id'] == run().run_id
    assert second['revision'] > first['revision'] > 0
    assert second['saved_ns'] >= first['saved_ns'] > 0
    assert result.data['comparisons'][0]['stock_reference'].source_ns == NOW - 1
    assert runner.snapshot() == result
    assert runner.store.identity() == second


def _admission_child(root, budget_proof, priority_proof, conn):
    from modules.leaps.admission import Admission, FileBudget, PriorityGate
    folder = Path(root) / 'leaps'
    gate = Admission(FileBudget(root, folder / 'synthetic-shared.lock', folder / 'synthetic-shared.json', budget_proof),
                     PriorityGate(root, priority_proof), lambda: NOW, atomic_writer=write_json_atomic)
    decision = gate.try_acquire('market')
    conn.send((decision.allowed, decision.reason))
    if decision.allowed:
        conn.recv()
        gate.release('market')
    conn.close()


def test_admission_cross_process_request_lease_and_priority_reservation_order(tmp_path):
    gate, clock, budget, priority = certified(tmp_path)
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_admission_child, args=(str(tmp_path), budget.proof, priority.proof, child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() == (True, 'allowed')
        # Reservation completed. Higher priority can start before HTTP finishes.
        priority.publish_owners({'stock': True, 'bi': False, 'alert': False}, clock.now)
        assert gate.try_acquire('sec').reason == 'request_busy'
        parent.send('finish'); process.join(10)
        assert process.exitcode == 0
        assert gate.try_acquire('market').reason == 'priority_active'
        assert json.loads(budget.state_path.read_text())['count'] == 1
    finally:
        if process.is_alive():
            process.terminate(); process.join(10)
        parent.close(); child.close()


def test_budget_certificate_binds_exact_state_path(tmp_path):
    gate, _, budget, _ = certified(tmp_path)
    alternate = budget.root / 'different-budget.json'
    write_json_atomic(alternate, {'window': NOW // (60 * 10**9), 'count': 0})
    budget.state_path = alternate
    assert not gate.try_acquire('market').allowed


def test_unknown_existing_namespace_is_not_assumed_private(tmp_path):
    (tmp_path / 'leaps').mkdir()
    with pytest.raises(OSError):
        runtime().LeapsStore(tmp_path, write_json_atomic)


def test_partial_priority_update_cannot_refresh_other_stale_owners(tmp_path):
    gate, clock, _, priority = certified(tmp_path)
    clock.now += 6 * 10**9
    with pytest.raises(ValueError):
        priority.publish_owners({'stock': False}, clock.now)
    assert not gate.try_acquire('market').allowed


def test_corrupt_cursor_fails_before_next_transport(tmp_path):
    runner, transport, _, _ = make_runner(tmp_path)
    runner.start(run())
    queued = runner.snapshot()
    runner.store.publish(replace(queued, cursor='{"version":1}'))
    result = runner.run_slice()
    assert result.status == 'failed' and transport.calls == []
    assert 'checkpoint_mismatch' in result.reasons


def test_concurrent_start_returns_existing_run_and_no_io(tmp_path):
    runner, calls, clock, gate = make_runner(tmp_path)
    expected = runner.start(run())
    second, calls2, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    actual = second.start(replace(run(), run_id='competing'))
    assert actual == expected
    assert calls.calls == calls2.calls == []


def _worker_owner_child(root, conn):
    from modules.leaps.admission import OSLease
    with OSLease(Path(root) / 'leaps' / 'worker.lock'):
        conn.send('owned')
        conn.recv()
    conn.close()


def test_worker_owner_blocks_other_process_without_blocking_pause(tmp_path):
    runner, transport, _, _ = make_runner(tmp_path)
    queued = runner.start(run())
    ctx = multiprocessing.get_context('spawn')
    parent, child = ctx.Pipe()
    process = ctx.Process(target=_worker_owner_child, args=(str(tmp_path), child))
    process.start()
    try:
        assert parent.poll(10) and parent.recv() == 'owned'
        assert runner.run_slice() == queued
        assert runner.start(replace(run(), run_id='competing')) == queued
        assert runner.pause(run().run_id).status == 'paused'
        assert transport.calls == []
        parent.send('release'); process.join(10)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate(); process.join(10)
        parent.close(); child.close()


def test_explicit_fresh_reconciliation_required_before_replacement_can_dispatch(tmp_path):
    runner, transport, clock, gate = make_runner(tmp_path)
    runner.start(run()); transport.market_status = 429
    assert runner.run_slice().status == 'paused'
    failure = runner.authority_states()['market']
    replacement, calls, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    proof = runtime().AuthorityReconciliation('market', 'rate_limited', int(failure['observed_ns']),
                                               NOW, NOW + 10**9, ('synthetic-fresh-budget-reconciliation',))
    with pytest.raises(LeapsDataError):
        runner.reconcile_authority('market', replace(proof, valid_until_ns=NOW - 1), replacement.provider)
    assert runner.authority_states()['market']['reason'] == 'rate_limited'
    runner.reconcile_authority('market', proof, replacement.provider)
    assert runner.run_slice().status == 'paused' and calls.calls == []
    runner.resume(run().run_id)
    assert runner.run_slice().status == 'complete'
    assert len(calls.calls) == 6


@pytest.mark.parametrize('allocation,pending_scope,history_before', [(50, 'market', 0), (60, 'sec', 1)])
def test_persisted_underlying_pause_retains_original_quote_and_history(tmp_path, allocation, pending_scope, history_before):
    from tests.fixtures.leaps.discovery import completion
    gate, clock, _, priority = certified(tmp_path, allocation)
    runner, transport, _, _ = make_runner(tmp_path, gate=gate, clock=clock, sec=True, completion_producer=completion)
    runner.start(run())
    partial = runner.run_slice()
    assert partial.status == 'paused' and partial.coverage['underlyings_loaded'] == 0
    assert partial.data['pending_underlyings']['SOFI']['authority'] == pending_scope
    assert sum('/aggs/' in url for url in transport.calls) == history_before
    clock.now += 60 * 10**9
    priority.publish_owners({'stock': False, 'bi': False, 'alert': False}, clock.now)
    def no_reacquire(*args):
        pytest.fail('completion proof must come from serialized checkpoint')
    restored, more, _, _ = make_runner(tmp_path, gate=gate, clock=clock, sec=True, completion_producer=no_reacquire)
    restored.resume(run().run_id)
    final = restored.run_slice()
    assert final.status == 'complete' and final.coverage['parsed'] == 1
    assert not final.data['pending_underlyings']
    assert not any('/nbbo/' in url for url in more.calls)
    assert sum('/aggs/' in url for url in more.calls) == 1 - history_before
    row = final.data['comparisons'][0]
    assert row['stock_reference'].source_ns == NOW - 1
    assert not row['assessment'].comparison_eligible
    assert final.data['underlyings']['SOFI'].indicators['indicators_meta']['completed_history_bars'] == 1


def test_parallel_start_during_initial_publication_returns_same_existing_run(tmp_path):
    entered, finish = threading.Event(), threading.Event()
    first_write = [True]
    def writer(path, payload):
        if first_write[0]:
            first_write[0] = False
            entered.set()
            assert finish.wait(3)
        write_json_atomic(path, payload)
    store = runtime().LeapsStore(tmp_path, writer)
    runner, _, clock, gate = make_runner(tmp_path, store=store)
    second, _, _, _ = make_runner(tmp_path, gate=gate, clock=clock)
    results, errors = [], []
    def start(which, spec):
        try:
            results.append(which.start(spec))
        except Exception as error:
            errors.append(error)
    thread = threading.Thread(target=start, args=(runner, run()))
    thread.start()
    assert entered.wait(3)
    competitor = threading.Thread(target=start, args=(second, replace(run(), run_id='competing')))
    competitor.start()
    finish.set()
    thread.join(5); competitor.join(5)
    assert not errors and len(results) == 2
    assert results[0].run == results[1].run == run()


@pytest.mark.parametrize('field', ['schema_version', 'methodology_version'])
def test_unsupported_runtime_version_is_not_resumed(tmp_path, field):
    runner, transport, _, _ = make_runner(tmp_path)
    runner.start(run()); runner.pause(run().run_id)
    saved = runner.snapshot()
    runner.store.publish(replace(saved, run=replace(saved.run, **{field: 'obsolete'})))
    with pytest.raises(LeapsDataError, match='unsupported_run_version'):
        runner.resume(run().run_id)
    assert transport.calls == []


def test_market_date_boundary_is_exact_at_ny_midnight(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    midnight = int(datetime(2026, 10, 14, tzinfo=ZoneInfo('America/New_York')).timestamp()) * 10**9
    runner, transport, clock, _ = make_runner(tmp_path)
    clock.now = midnight - 1
    assert runner.start(run()).status == 'queued'
    runner.pause(run().run_id)
    assert runner.resume(run().run_id).status == 'queued'
    runner.pause(run().run_id)
    clock.now = midnight
    with pytest.raises(LeapsDataError, match='stale_run'):
        runner.resume(run().run_id)
    assert runner.start(replace(run(), run_id='next-date', market_date=date(2026, 10, 14))).status == 'queued'
    assert transport.calls == []


def test_clock_regression_cannot_dispatch_next_safe_unit(tmp_path):
    runner, transport, clock, _ = make_runner(tmp_path)
    runner.start(run())
    def regress(_):
        clock.now -= 1
    transport.before = regress
    with pytest.raises(LeapsDataError, match='clock_regression'):
        runner.run_slice()
    assert len(transport.calls) == 1
    assert runner.snapshot().coverage['reference_seen'] == 1


def test_any_local_reservation_write_error_denies_and_releases_lease(tmp_path):
    gate, _, budget, _ = certified(tmp_path)
    def broken(*args):
        raise RuntimeError('synthetic unknown publisher failure')
    gate.writer = broken
    assert not gate.try_acquire('market').allowed
    lease = admission_module().OSLease(gate.root / 'request.lock')
    assert lease.acquire(); lease.release()
    assert json.loads(budget.state_path.read_text())['count'] == 1


def test_publish_new_run_does_not_leave_old_control_binding(tmp_path):
    runner, _, _, _ = make_runner(tmp_path)
    runner.start(run()); runner.pause(run().run_id)
    newer = snap('paused', run=replace(run(), run_id='new'))
    runner.store.publish(newer)
    assert runner.snapshot() == newer


def test_lost_existing_store_is_not_treated_as_empty(tmp_path):
    runner, _, _, _ = make_runner(tmp_path)
    runner.start(run())
    runner.store.path.unlink()
    with pytest.raises(LeapsDataError):
        runner.snapshot()


def test_created_namespace_has_private_native_permissions(tmp_path):
    import os
    folder = admission_module().private_namespace(tmp_path)
    if os.name == 'posix':
        assert folder.stat().st_mode & 0o777 == 0o700
    else:
        import ctypes
        from ctypes import wintypes
        adv = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        get_info = adv.GetNamedSecurityInfoW
        get_info.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        get_info.restype = wintypes.DWORD
        stringify = adv.ConvertSecurityDescriptorToStringSecurityDescriptorW
        stringify.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                              ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
        stringify.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        sd, text = ctypes.c_void_p(), wintypes.LPWSTR()
        assert get_info(str(folder), 1, 4, None, None, None, None, ctypes.byref(sd)) == 0
        try:
            assert stringify(sd, 1, 4, ctypes.byref(text), None)
            try:
                import re
                assert text.value.startswith('D:P')
                assert set(re.findall(r';;;([^)]*)\)', text.value)) == {'OW', 'SY'}
            finally:
                kernel.LocalFree(text)
        finally:
            kernel.LocalFree(sd)
