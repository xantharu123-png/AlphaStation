"""Private atomic LEAPS state and explicit worker lifecycle. No scheduler."""
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
import os
import re
import time
from zoneinfo import ZoneInfo

from . import models
from .admission import OSLease, private_namespace, _private_permissions
from .models import LeapsDataError, LeapsSnapshot, RunSpec
from .provider import MassiveLeapsProvider, OptionQuote, StockQuote, VerifiedSession, DailyCompletionProof, authority_error


_TYPES = {cls.__name__: cls for cls in (
    models.OptionContract, models.QuoteObservation, models.UnderlyingObservation,
    models.QuoteAssessment, models.GreeksObservation, models.CapabilityEvidence,
    models.PageResult, models.UnderlyingEvidence, models.ExpiryResult,
    RunSpec, LeapsSnapshot, OptionQuote, StockQuote, VerifiedSession, DailyCompletionProof)}
_STATUSES = {'queued', 'running', 'paused', 'partial', 'complete', 'failed'}
SUPPORTED_SCHEMA_VERSION = '1'
SUPPORTED_METHODOLOGY_VERSION = '1'


def _error(code='invalid_store'):
    return LeapsDataError(code, 'provider')


def _encode(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if isinstance(value, Decimal) and value.is_finite():
        return {'t': 'decimal', 'v': str(value)}
    if type(value) is date:
        return {'t': 'date', 'v': value.isoformat()}
    if type(value).__name__ in _TYPES and type(value) is _TYPES[type(value).__name__]:
        return {'t': type(value).__name__, 'v': {f.name: _encode(getattr(value, f.name)) for f in fields(value)}}
    if isinstance(value, Mapping) and all(type(k) is str for k in value):
        return {'t': 'map', 'v': {k: _encode(v) for k, v in value.items()}}
    if type(value) in (tuple, list):
        return [_encode(item) for item in value]
    raise _error('unsafe_store_value')


def _decode(value):
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if type(value) is list:
        return tuple(_decode(item) for item in value)
    if type(value) is not dict or set(value) != {'t', 'v'}:
        raise _error()
    kind, body = value['t'], value['v']
    if kind == 'decimal':
        if not isinstance(body, str) or not Decimal(body).is_finite():
            raise _error()
        return Decimal(body)
    if kind == 'date':
        return date.fromisoformat(body)
    if type(body) is not dict:
        raise _error()
    if kind == 'map':
        return {k: _decode(v) for k, v in body.items()}
    cls = _TYPES.get(kind)
    if cls is None or set(body) != {f.name for f in fields(cls)}:
        raise _error()
    return cls(**{k: _decode(v) for k, v in body.items()})


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise _error()
        result[key] = value
    return result


def _validate_snapshot(snapshot):
    if not isinstance(snapshot, LeapsSnapshot) or snapshot.status not in _STATUSES:
        raise _error()
    run = snapshot.run
    if (not isinstance(run, RunSpec) or type(run.market_date) is not date
            or not all(isinstance(getattr(run, f), str) and 0 < len(getattr(run, f)) <= 128
                       for f in ('run_id', 'universe_revision', 'schema_version', 'methodology_version'))
            or not isinstance(run.filters, Mapping)):
        raise _error('invalid_run')
    if not isinstance(snapshot.data, Mapping) or not isinstance(snapshot.coverage, Mapping):
        raise _error()
    if not isinstance(snapshot.reasons, tuple) or any(not isinstance(r, str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}', r) for r in snapshot.reasons):
        raise _error()
    if snapshot.cursor is not None and not isinstance(snapshot.cursor, str):
        raise _error()
    if not isinstance(snapshot.sources, tuple):
        raise _error()


class _StoreLease(OSLease):
    """Bounded contention wait for short store transactions, never for HTTP."""
    def __enter__(self):
        deadline = time.monotonic() + 2
        while not self.acquire():
            if time.monotonic() >= deadline:
                raise BlockingIOError('LEAPS store busy')
            time.sleep(0.005)
        return self


class LeapsStore:
    """One versioned replace, logical partial/final partitions; no power-loss claim.

    Atomic writer is injected (normally write_json_atomic); it must preserve the
    old file on failure. Lock covers the complete read/modify/write operation.
    """
    def __init__(self, root, atomic_writer, *, clock_ns=time.time_ns):
        self.root = private_namespace(root)
        self.atomic_writer = atomic_writer
        self.path = self.root / 'state.json'
        self.clock_ns = clock_ns

    def lock(self):
        return _StoreLease(self.root / 'store.lock')

    def _read(self):
        if not self.path.exists():
            if (self.root / 'state.initialized').exists():
                raise _error('lost_store')
            return dict(version=1, latest=None, last_complete=None, control=None, authorities={}, revision=0, saved_ns=None)
        try:
            if self.path.is_symlink():
                raise _error()
            raw = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(_error()))
            if type(raw) is not dict or set(raw) != {'version', 'latest', 'last_complete', 'control', 'authorities', 'revision', 'saved_ns'} or type(raw['version']) is not int or raw['version'] != 1:
                raise _error()
            state = dict(raw, latest=_decode(raw['latest']), last_complete=_decode(raw['last_complete']))
            for key in ('latest', 'last_complete'):
                if state[key] is not None:
                    _validate_snapshot(state[key])
            if state['last_complete'] is not None and state['last_complete'].status != 'complete':
                raise _error()
            if state['control'] is not None and (type(state['control']) is not dict or set(state['control']) != {'run_id', 'action'} or state['control']['action'] not in ('run', 'pause') or state['latest'] is None or state['control']['run_id'] != state['latest'].run.run_id):
                raise _error()
            if type(state['revision']) is not int or state['revision'] < 1 or not isinstance(state['saved_ns'], str) or not state['saved_ns'].isdigit():
                raise _error()
            if type(state['authorities']) is not dict:
                raise _error()
            for scope, record in state['authorities'].items():
                if (scope not in ('market', 'sec', 'market:stock_quotes', 'market:option_quotes', 'market:option_reference', 'market:stock_reference', 'market:stock_history') or type(record) is not dict
                        or set(record) != {'reason', 'run_id', 'cursor', 'observed_ns', 'evidence_ids'}
                        or record['reason'] not in ('authorization_denied', 'rate_limited', None)
                        or (record['run_id'] is not None and not isinstance(record['run_id'], str))
                        or (record['cursor'] is not None and not isinstance(record['cursor'], str))
                        or not isinstance(record['observed_ns'], str) or not record['observed_ns'].isdigit()
                        or type(record['evidence_ids']) is not list or any(not isinstance(e, str) or not e for e in record['evidence_ids'])):
                    raise _error()
            return state
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise _error() from None

    def _write(self, state):
        for key in ('latest', 'last_complete'):
            if state[key] is not None:
                _validate_snapshot(state[key])
        saved = self.clock_ns()
        if type(saved) is not int or saved <= 0:
            raise _error('invalid_saved_clock')
        encoded = dict(state, latest=_encode(state['latest']), last_complete=_encode(state['last_complete']),
                       revision=state['revision'] + 1, saved_ns=str(saved))
        json.dumps(encoded, allow_nan=False)
        if self.path.is_symlink():
            raise _error()
        marker = self.root / 'state.initialized'
        if not marker.exists():
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            _private_permissions(marker)
        self.atomic_writer(self.path, encoded)

    def publish(self, snapshot):
        _validate_snapshot(snapshot)
        _encode(snapshot)  # Reject unsafe input BEFORE invoking the writer.
        with self.lock():
            state = self._read()
            state['latest'] = snapshot
            if state['control'] is not None and state['control']['run_id'] != snapshot.run.run_id:
                state['control'] = {'run_id': snapshot.run.run_id, 'action': 'pause'}
            if snapshot.status == 'complete':
                state['last_complete'] = snapshot
            self._write(state)

    def load(self):
        with self.lock():
            return self._read()['latest']

    def load_complete(self):
        with self.lock():
            return self._read()['last_complete']

    def identity(self):
        """Publication identity, NOT a new quote/source freshness timestamp."""
        with self.lock():
            state = self._read()
            return {'run_id': state['latest'].run.run_id if state['latest'] else None,
                    'revision': state['revision'],
                    'saved_ns': int(state['saved_ns']) if state['saved_ns'] is not None else None}


class AuthorityProvider:
    """Public Task3 proxy: persist original scope errors before discovery handles them.

    Stored authority failures are raised BEFORE admission, preserving SEC's
    original code (not generic admission_denied) across provider recreation.
    Only the SAME supplied provider is called; private flags are never read.
    """
    _SCOPES = {'list_contract_page': 'market', 'list_listing_page': 'market',
               'chain_page': 'market', 'underlying_quote': 'market',
               'daily_history': 'market', 'company_facts': 'sec'}
    _RESOURCES = {'list_contract_page':'market:option_reference', 'list_listing_page':'market:stock_reference',
                  'chain_page':'market:option_quotes','underlying_quote':'market:stock_quotes',
                  'daily_history':'market:stock_history','company_facts':'sec'}

    def __init__(self, provider, store, admission, clock_ns):
        if not isinstance(provider, MassiveLeapsProvider) or provider.admission is not admission:
            raise _error('unbound_provider_admission')
        self.provider, self.store, self.admission, self.clock_ns = provider, store, admission, clock_ns

    def __getattr__(self, name):
        if name == 'parse_chain_item':
            return self.provider.parse_chain_item
        if name not in self._SCOPES:
            raise AttributeError(name)
        def call(*args, **kwargs):
            scope = self._SCOPES[name]
            with self.store.lock():
                state = self.store._read()
                for authority in (scope, self._RESOURCES[name]):
                    record = state['authorities'].get(authority)
                    if record and record['reason']:
                        raise authority_error(record['reason'], authority)
                # Pause requests are checked before every physical public unit,
                # including history/SEC inside a discovery underlying safe unit.
                if state['control'] and state['control']['action'] == 'pause':
                    raise _error('admission_denied')
            try:
                return getattr(self.provider, name)(*args, **kwargs)
            except LeapsDataError as error:
                if error.code in ('authorization_denied', 'rate_limited'):
                    with self.store.lock():
                        state = self.store._read()
                        latest = state['latest']
                        authority = getattr(error, 'authority', scope)
                        state['authorities'][authority] = {
                            'reason': error.code, 'run_id': latest.run.run_id if latest else None,
                            'cursor': latest.cursor if latest else None,
                            'observed_ns': str(self.clock_ns()), 'evidence_ids': []}
                        self.store._write(state)
                raise
        return call


@dataclass(frozen=True)
class AuthorityReconciliation:
    """Trusted evidence producer DTO, never populated from admin JSON/booleans.

    Binds the exact persisted failure and an explicitly authorized replacement
    provider supplied by composition. Does not itself establish admission.
    """
    authority: str
    reason: str
    failure_observed_ns: int
    verified_ns: int
    valid_until_ns: int
    evidence_ids: tuple[str, ...]


class LeapsRunner:
    """Acknowledgments do no provider IO. Call run_slice ONLY on a worker.

    A new instance has no worker authorization until explicit start/resume.
    snapshot() loads saved, dated evidence without refresh or discovery. Its
    stored eligibility must be expired by Task6/7 against ORIGINAL quote clocks.
    """
    def __init__(self, provider, store, admission, clock_ns):
        self.store, self.admission, self.clock_ns = store, admission, clock_ns
        if admission.root is not None and admission.root != store.root:
            raise _error('admission_namespace_mismatch')
        self._authorized_run = None
        self.provider = self._wrap(provider)

    def _wrap(self, provider):
        from .service import DiscoveryProvider
        from .underlying import CachedUnderlyingLoader
        if not isinstance(provider, DiscoveryProvider) or not isinstance(provider.underlying_loader, CachedUnderlyingLoader):
            raise _error('unverified_discovery_provider')
        raw = provider.market.provider if isinstance(provider.market, AuthorityProvider) else provider.market
        proxy = AuthorityProvider(raw, self.store, self.admission, self.clock_ns)
        loader = provider.underlying_loader
        # Clone only the run-scoped loader, not the actual provider/client.
        # Cursor v2 retains original quote/proof/completed-history acquisitions.
        bound_loader = CachedUnderlyingLoader(proxy, completion_producer=loader.completion_producer,
                                               configured_sec_user_agent=loader.user_agent)
        return DiscoveryProvider(proxy, terms_producer=provider.terms_producer,
                                 session_producer=provider.session_producer, underlying_loader=bound_loader)

    def _current_date(self):
        now = self.clock_ns()
        if type(now) is not int or now <= 0:
            raise _error('invalid_clock')
        return datetime.fromtimestamp(now // 10**9, timezone.utc).astimezone(ZoneInfo('America/New_York')).date()

    def _valid_run(self, run):
        from .service import _filters
        _validate_snapshot(LeapsSnapshot(run, 'queued', {}, {}, (), None, ()))
        _filters(run)  # Existing pure filter validation; no replacement policy.
        if run.schema_version != SUPPORTED_SCHEMA_VERSION or run.methodology_version != SUPPORTED_METHODOLOGY_VERSION:
            raise _error('unsupported_run_version')
        if run.market_date != self._current_date():
            raise _error('stale_run')

    def _owner(self):
        return OSLease(self.store.root / 'worker.lock')

    def snapshot(self):
        return self.store.load()

    def start(self, run):
        self._valid_run(run)
        with self.store.lock():
            state = self.store._read()
            previous = state['latest']
            if previous and previous.run.run_id == run.run_id:
                if previous.run != run:
                    raise _error('run_binding_mismatch')
                return previous
            if previous and previous.status in ('queued', 'running'):
                return previous
            # Nonblocking owner acquisition while store lock held: never wait
            # on a worker that itself needs this store. Concurrent starts first
            # serialize publication, so neither can acknowledge a nonexistent run.
            owner = self._owner()
            if not owner.acquire():
                if previous is None:
                    raise _error('worker_owner_unavailable')
                return previous
            try:
                result = LeapsSnapshot(run, 'queued', {}, {'progress': {'completed': 0, 'total': None, 'phase': 'queued'}}, (), None, ())
                state['latest'], state['control'] = result, {'run_id': run.run_id, 'action': 'run'}
                self.store._write(state)
                self._authorized_run = run
                return result
            finally:
                owner.release()

    def pause(self, run_id):
        with self.store.lock():
            state = self.store._read()
            current = state['latest']
            if current is None or current.run.run_id != run_id:
                raise _error('unknown_run')
            if current.status in ('complete', 'failed'):
                return current
            result = replace(current, status='paused', reasons=tuple(dict.fromkeys((*current.reasons, 'admin_pause'))))
            state['latest'], state['control'] = result, {'run_id': run_id, 'action': 'pause'}
            self.store._write(state)
            return result

    def resume(self, run_id):
        owner = self._owner()
        if not owner.acquire():
            return self.snapshot()
        try:
            with self.store.lock():
                state = self.store._read()
                current = state['latest']
                if current is None or current.run.run_id != run_id:
                    raise _error('unknown_run')
                self._valid_run(current.run)
                if current.status in ('complete', 'failed'):
                    raise _error('terminal_run')
                if current.cursor is not None:
                    from .service import _load
                    saved = _load(current.cursor, current.run)
                    if saved['phase'] in ('done', 'failed'):
                        raise _error('terminal_run')
                if state['authorities'].get('market', {}).get('reason'):
                    raise _error('authority_reconciliation_required')
                result = replace(current, status='queued', reasons=tuple(r for r in current.reasons if r not in ('admin_pause', 'slice_deadline', 'admission_denied')))
                state['latest'], state['control'] = result, {'run_id': run_id, 'action': 'run'}
                self.store._write(state)
                self._authorized_run = current.run
                return result
        finally:
            owner.release()

    def authority_states(self):
        with self.store.lock():
            return self.store._read()['authorities']

    def reconcile_authority(self, scope, proof, replacement):
        """Separate trusted composition operation; no default auto-unpause."""
        with self._owner():
            with self.store.lock():
                state = self.store._read()
                failure = state['authorities'].get(scope)
                now = self.clock_ns()
                if (not isinstance(proof, AuthorityReconciliation) or not failure or not failure['reason']
                        or proof.authority != scope or proof.reason != failure['reason']
                        or type(proof.failure_observed_ns) is not int or str(proof.failure_observed_ns) != failure['observed_ns']
                        or type(proof.verified_ns) is not int or type(proof.valid_until_ns) is not int
                        or not proof.failure_observed_ns <= proof.verified_ns <= now <= proof.valid_until_ns
                        or not isinstance(proof.evidence_ids, tuple) or not proof.evidence_ids
                        or any(not isinstance(e, str) or not e.strip() for e in proof.evidence_ids)):
                    raise _error('unverified_reconciliation')
                self.admission.budget.allocation(now)
                wrapped = self._wrap(replacement)
                state['authorities'][scope] = dict(failure, reason=None, evidence_ids=list(proof.evidence_ids))
                self.store._write(state)
                self.provider = wrapped
                self._authorized_run = None

    @staticmethod
    def _progress(snapshot):
        coverage = dict(snapshot.coverage)
        completed = coverage.get('chain_complete', 0) + coverage.get('chain_failed', 0)
        phase = snapshot.status
        if snapshot.cursor:
            # Already produced/validated by discovery. Never accept browser cursor.
            from .service import _load
            phase = _load(snapshot.cursor, snapshot.run)['phase']
        coverage['progress'] = {'completed': completed, 'total': coverage.get('underlyings_total'), 'phase': phase}
        return replace(snapshot, coverage=coverage)

    def run_slice(self):
        from .service import discover
        owner = self._owner()
        if not owner.acquire():
            return self.snapshot()
        try:
            started = self.clock_ns()
            while True:
                with self.store.lock():
                    state = self.store._read()
                    current = state['latest']
                    if (current is None or self._authorized_run != current.run or not state['control']
                            or state['control']['action'] != 'run' or current.status in ('complete', 'failed')):
                        return current
                    self._valid_run(current.run)
                    now = self.clock_ns()
                    if now < started:
                        raise _error('clock_regression')
                    if now - started >= 900 * 10**9:
                        result = replace(current, status='partial', reasons=tuple(dict.fromkeys((*current.reasons, 'slice_deadline'))))
                        state['latest'], state['control']['action'] = result, 'pause'
                        self.store._write(state)
                        return result
                # NO store/priority/shared-budget lock is held across discovery.
                try:
                    result = self._progress(discover(self.provider, current.run, current.cursor))
                except LeapsDataError as error:
                    result = replace(current, status='failed', reasons=tuple(dict.fromkeys((*current.reasons, error.code))))
                with self.store.lock():
                    state = self.store._read()
                    if state['latest'].run != current.run:
                        raise _error('run_owner_changed')
                    if state['control']['action'] == 'pause' and result.status not in ('complete', 'failed'):
                        result = replace(result, status='paused', reasons=tuple(dict.fromkeys((*result.reasons, 'admin_pause'))))
                    state['latest'] = result
                    for record in state['authorities'].values():
                        if record['reason'] and record['run_id'] == current.run.run_id:
                            record['cursor'] = result.cursor
                    if result.status == 'complete':
                        state['last_complete'] = result
                    if result.status != 'running':
                        state['control']['action'] = 'pause'
                    self.store._write(state)
                if result.status != 'running':
                    return result
        finally:
            owner.release()
