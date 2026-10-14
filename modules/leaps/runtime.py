"""Private atomic LEAPS state and explicit worker lifecycle. No scheduler."""
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, fields, replace
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import math
import os
import re
import time
from hashlib import sha256
from pathlib import Path
from uuid import uuid4
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
    optional={'stock_reference_capital','stock_share_count'} if cls is models.ExpiryResult else set()
    expected={f.name for f in fields(cls)} if cls else set()
    if cls is None or set(body)-expected or expected-set(body)-optional:
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


@dataclass(frozen=True)
class _SnapshotReference:
    run: RunSpec
    status: str
    coverage: object
    reasons: tuple
    blob: str
    pending: Path | None = None


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
        self.blobs = self.root / 'snapshots'
        self._cache = {}
        self._migrate()

    def lock(self):
        return _StoreLease(self.root / 'store.lock')

    def _raw(self):
        if not self.path.exists():
            if (self.root / 'state.initialized').exists():
                raise _error('lost_store')
            return dict(version=2, latest=None, last_complete=None, control=None, authorities={}, revision=0, saved_ns=None,clock_watermark_ns='0')
        try:
            if self.path.is_symlink():
                raise _error()
            raw = json.loads(self.path.read_text(encoding='utf-8'), object_pairs_hook=_unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(_error()))
            if (type(raw) is not dict or type(raw.get('version')) is not int or raw['version'] not in (1,2)
                    or set(raw)!={'version','latest','last_complete','control','authorities','revision','saved_ns'} | ({'clock_watermark_ns'} if raw['version']==2 else set())):
                raise _error()
            return raw
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError):
            raise _error() from None

    def _read_metadata(self, raw=None):
        try:
            raw=self._raw() if raw is None else raw
            if raw['version']!=2:
                raise _error('legacy_store_migration_required')
            watermark=raw.get('clock_watermark_ns')
            if not isinstance(watermark,str) or not watermark.isdigit() or len(watermark)>20:
                raise _error('invalid_clock_watermark')
            def reference(value):
                if value is None:
                    return None
                if (type(value) is not dict or set(value)!={'run','status','coverage','reasons','blob'}
                        or not isinstance(value['blob'],str) or not re.fullmatch('[0-9a-f]{64}',value['blob'])):
                    raise _error()
                ref=_SnapshotReference(_decode(value['run']),value['status'],_decode(value['coverage']),
                                       _decode(value['reasons']),value['blob'])
                _validate_snapshot(LeapsSnapshot(ref.run,ref.status,{},ref.coverage,ref.reasons,None,()))
                return ref
            state=dict(raw,latest=reference(raw['latest']),last_complete=reference(raw['last_complete']))
            for key in ('latest', 'last_complete'):
                if state[key] is not None and state[key].pending is not None:
                    raise _error()
            if state['last_complete'] is not None and state['last_complete'].status != 'complete':
                raise _error()
            if state['control'] is not None and (type(state['control']) is not dict or set(state['control']) != {'run_id', 'action'} or state['control']['action'] not in ('run', 'pause') or state['latest'] is None or state['control']['run_id'] != state['latest'].run.run_id):
                raise _error()
            if (type(state['revision']) is not int or state['revision'] < 0 or
                    (state['revision']>0 and (not isinstance(state['saved_ns'],str) or not state['saved_ns'].isdigit()))):
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

    def _blob_path(self, identity):
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{64}',identity):
            raise _error()
        if self.blobs.is_symlink():
            raise _error()
        return self.blobs/(identity+'.json')

    def prepare(self, snapshot):
        """Large serialization/hash/publication runs BEFORE metadata lock."""
        if snapshot is None or isinstance(snapshot,_SnapshotReference):
            return snapshot
        _validate_snapshot(snapshot)
        encoded=_encode(snapshot)
        body=json.dumps(encoded,allow_nan=False).encode('utf-8')
        identity=sha256(body).hexdigest()
        target=self._blob_path(identity)
        if target.exists():
            if target.is_symlink() or not target.is_file() or sha256(target.read_bytes()).hexdigest()!=identity:
                raise _error()
        self.blobs.mkdir(exist_ok=True)
        _private_permissions(self.blobs, directory=True)
        # Pending names are never garbage collected or referenced by metadata.
        # Another publisher cannot prune a not-yet-committed snapshot.
        pending=self.blobs/('.pending-'+uuid4().hex+'.json')
        self.atomic_writer(pending,encoded)
        _private_permissions(pending)
        if sha256(pending.read_bytes()).hexdigest()!=identity:
            raise _error()
        return _SnapshotReference(snapshot.run,snapshot.status,snapshot.coverage,snapshot.reasons,identity,pending)

    def _write(self, state):
        # All large snapshots must already be prepared outside this short lock.
        refs={key:state[key] for key in ('latest','last_complete')}
        if any(value is not None and not isinstance(value,_SnapshotReference) for value in refs.values()):
            raise _error('unprepared_snapshot')
        saved = self.clock_ns()
        if type(saved) is not int or saved <= 0:
            raise _error('invalid_saved_clock')
        previous=self._raw()
        old_ids={previous[key]['blob'] for key in ('latest','last_complete')
                 if previous['version']==2 and previous[key] is not None}
        def encoded_ref(ref):
            if ref is None:
                return None
            target=self._blob_path(ref.blob)
            if ref.pending is not None and ref.pending.exists():
                if target.exists():
                    if target.is_symlink() or not target.is_file():
                        raise _error()
                    ref.pending.unlink()
                else:
                    os.replace(ref.pending,target)
            if not target.is_file() or target.is_symlink():
                raise _error()
            return dict(run=_encode(ref.run),status=ref.status,coverage=_encode(ref.coverage),
                        reasons=_encode(ref.reasons),blob=ref.blob)
        # Publication is already a known decision clock, including before the
        # first GET. It is only a conservative floor, never a source refresh.
        watermark=max(int(previous.get('clock_watermark_ns','0')),int(state.get('clock_watermark_ns','0')),saved)
        encoded=dict(state,version=2,latest=encoded_ref(refs['latest']),last_complete=encoded_ref(refs['last_complete']),
                     revision=state['revision']+1,saved_ns=str(saved),clock_watermark_ns=str(watermark))
        self._read_metadata(encoded)  # Validate BEFORE atomic metadata replacement.
        json.dumps(encoded, allow_nan=False)
        if self.path.is_symlink():
            raise _error()
        marker = self.root / 'state.initialized'
        if not marker.exists():
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
            _private_permissions(marker)
        self.atomic_writer(self.path, encoded)
        live_ids={ref.blob for ref in refs.values() if ref is not None}
        live_ids.update(record['cursor'][len('blob:'):] for record in state['authorities'].values()
                        if isinstance(record.get('cursor'),str) and record['cursor'].startswith('blob:'))
        # Exact former committed owned targets only; no glob/recursive cleanup.
        # Readers opened their handles under the same metadata lock. POSIX keeps
        # their handle alive; Windows denies deletion, so retain conservatively.
        for identity in old_ids-live_ids:
            target=self._blob_path(identity)
            if target.is_file() and not target.is_symlink():
                try:
                    target.unlink()
                except OSError:
                    pass

    def _migrate(self):
        """Explicit local cache upgrade; no decode/hash under metadata lock."""
        if not self.path.exists():
            return
        if self.path.is_symlink():
            raise _error()
        original=self.path.read_bytes()
        try:
            raw=json.loads(original,object_pairs_hook=_unique,parse_constant=lambda _:(_ for _ in ()).throw(_error()))
        except (ValueError,TypeError):
            raise _error() from None
        if not isinstance(raw,dict) or raw.get('version')!=1:
            return  # Normal read validates v2/corrupt schemas.
        if set(raw)!={'version','latest','last_complete','control','authorities','revision','saved_ns'}:
            raise _error()
        state=dict(raw)
        if not isinstance(raw['saved_ns'],str) or not raw['saved_ns'].isdigit():
            raise _error()
        # A legacy cache has no read history. Its known publication clock is a
        # conservative floor, never a replacement quote/source timestamp.
        state['clock_watermark_ns']=raw['saved_ns']
        prepared={}
        for key in ('latest','last_complete'):
            snapshot=_decode(raw[key])
            if snapshot is not None:
                _validate_snapshot(snapshot)
                if key=='last_complete' and snapshot.status!='complete':
                    raise _error()
            identity=json.dumps(raw[key],allow_nan=False)
            if identity not in prepared:
                prepared[identity]=self.prepare(snapshot)
            state[key]=prepared[identity]
        with self.lock():
            if self.path.read_bytes()!=original:
                raise _error('migration_raced')
            self._write(state)
            self._read_metadata()  # Validate migrated control/authority schema.

    def read(self, *, metadata=False):
        """Capture immutable handles while locked; validate/decode after unlock."""
        handles={}
        try:
            with self.lock():
                state=self._read_metadata()
                if metadata:
                    return state
                for key in ('latest','last_complete'):
                    ref=state[key]
                    if ref is not None and ref.blob not in handles:
                        path=self._blob_path(ref.blob)
                        if path.is_symlink() or not path.is_file():
                            raise _error()
                        handles[ref.blob]=path.open('rb')
            decoded={}
            for identity,handle in handles.items():
                body=handle.read()
                if sha256(body).hexdigest()!=identity:
                    raise _error()
                snapshot=self._cache.get(identity)
                if snapshot is None:
                    snapshot=_decode(json.loads(body,object_pairs_hook=_unique,
                        parse_constant=lambda _:(_ for _ in ()).throw(_error())))
                    _validate_snapshot(snapshot)
                decoded[identity]=snapshot
            self._cache=decoded  # At most current latest + complete, never history.
            for key in ('latest','last_complete'):
                ref=state[key]
                if ref is None:
                    continue
                snapshot=decoded[ref.blob]
                if snapshot.run!=ref.run:
                    raise _error()
                if (snapshot.status,snapshot.coverage,snapshot.reasons)!=(ref.status,ref.coverage,ref.reasons):
                    snapshot=replace(snapshot,status=ref.status,coverage=ref.coverage,reasons=ref.reasons)
                state[key]=snapshot
            return state
        except (OSError,ValueError,TypeError,KeyError,OverflowError,RecursionError):
            raise _error() from None
        finally:
            for handle in handles.values():
                handle.close()

    def _read(self):
        return self.read()

    def observe_clock(self, clock_ns):
        """Sample trusted decision time at lock order, never refresh source time."""
        if not callable(clock_ns):
            raise _error('invalid_clock')
        with self.lock():
            state=self._read_metadata()
            now=clock_ns()
            if type(now) is not int or not 0<now<10**20:
                raise _error('invalid_clock')
            previous=int(state['clock_watermark_ns'])
            if now>previous:
                state['clock_watermark_ns']=str(now)
                self._write(state)
            return now,now<previous

    def publish(self, snapshot):
        _validate_snapshot(snapshot)
        _encode(snapshot)  # Reject unsafe input BEFORE invoking the writer.
        prepared=self.prepare(snapshot)
        with self.lock():
            state = self._read_metadata()
            state['latest'] = prepared
            if state['control'] is not None and state['control']['run_id'] != snapshot.run.run_id:
                state['control'] = {'run_id': snapshot.run.run_id, 'action': 'pause'}
            if snapshot.status == 'complete':
                state['last_complete'] = prepared
            self._write(state)

    def load(self):
        return self.read()['latest']

    def load_complete(self):
        return self.read()['last_complete']

    def identity(self):
        """Publication identity, NOT a new quote/source freshness timestamp."""
        with self.lock():
            state = self._read_metadata()
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
                state = self.store._read_metadata()
                for authority in (scope, self._RESOURCES[name]):
                    record = state['authorities'].get(authority)
                    if record and record['reason']:
                        raise authority_error(record['reason'], authority)
                # Pause requests are checked before every physical public unit,
                # including history/SEC inside a discovery underlying safe unit.
                if state['control'] and state['control']['action'] == 'pause':
                    raise _error('admission_denied')
                bound_run=state['latest'].run if state['latest'] else None
            @contextmanager
            def attempt_scope():
                # Same lock linearizes a newly admitted request ticket against
                # pause/new-run publication. Never held over transport/body IO.
                with self.store.lock():
                    current=self.store._read_metadata()
                    current_run=current['latest'].run if current['latest'] else None
                    if (current_run!=bound_run or
                            (current['control'] and current['control']['action']=='pause')):
                        raise _error('admission_denied')
                    yield
            try:
                with self.provider.bound_attempts(attempt_scope):
                    return getattr(self.provider, name)(*args, **kwargs)
            except LeapsDataError as error:
                if error.code in ('authorization_denied', 'rate_limited'):
                    with self.store.lock():
                        state = self.store._read_metadata()
                        latest = state['latest']
                        authority = getattr(error, 'authority', scope)
                        state['authorities'][authority] = {
                            'reason': error.code, 'run_id': latest.run.run_id if latest else None,
                            'cursor': 'blob:'+latest.blob if latest else None,
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

    @staticmethod
    def _ack(snapshot):
        if snapshot is None or isinstance(snapshot,LeapsSnapshot):
            return snapshot
        return LeapsSnapshot(snapshot.run,snapshot.status,{},snapshot.coverage,snapshot.reasons,None,())

    def start(self, run):
        self._valid_run(run)
        with self.store.lock():
            state = self.store._read_metadata()
            previous = state['latest']
            if previous and previous.run.run_id == run.run_id:
                if previous.run != run:
                    raise _error('run_binding_mismatch')
                return self._ack(previous)
            if previous and previous.status in ('queued', 'running'):
                return self._ack(previous)
            # Nonblocking owner acquisition while store lock held: never wait
            # on a worker that itself needs this store. Concurrent starts first
            # serialize publication, so neither can acknowledge a nonexistent run.
            owner = self._owner()
            if not owner.acquire():
                if previous is None:
                    raise _error('worker_owner_unavailable')
                return self._ack(previous)
            try:
                result = LeapsSnapshot(run, 'queued', {}, {'progress': {'completed': 0, 'total': None, 'phase': 'queued'}}, (), None, ())
                state['latest'], state['control'] = self.store.prepare(result), {'run_id': run.run_id, 'action': 'run'}
                self.store._write(state)
                # Only an accepted NEW run gets fresh acquisitions. Start does
                # no IO; resumed checkpoints and same-run caches stay intact.
                self.provider = self._wrap(self.provider)
                self._authorized_run = run
                return result
            finally:
                owner.release()

    def pause(self, run_id):
        with self.store.lock():
            state = self.store._read_metadata()
            current = state['latest']
            if current is None or current.run.run_id != run_id:
                raise _error('unknown_run')
            if current.status in ('complete', 'failed'):
                return self._ack(current)
            result = replace(current, status='paused', reasons=tuple(dict.fromkeys((*current.reasons, 'admin_pause'))))
            state['latest'], state['control'] = result, {'run_id': run_id, 'action': 'pause'}
            self.store._write(state)
            return self._ack(result)

    def resume(self, run_id):
        owner = self._owner()
        if not owner.acquire():
            return self.snapshot()
        try:
            # Cursor validation may be large, so do it outside metadata lock.
            loaded=self.store.load()
            if loaded is not None and loaded.cursor is not None:
                from .service import _load
                saved=_load(loaded.cursor,loaded.run)
                if saved['phase'] in ('done','failed'):
                    raise _error('terminal_run')
            with self.store.lock():
                state = self.store._read_metadata()
                current = state['latest']
                if current is None or current.run.run_id != run_id:
                    raise _error('unknown_run')
                self._valid_run(current.run)
                if current.status in ('complete', 'failed'):
                    raise _error('terminal_run')
                if loaded is None or current.run!=loaded.run:
                    raise _error('run_owner_changed')
                if state['authorities'].get('market', {}).get('reason'):
                    raise _error('authority_reconciliation_required')
                result = replace(current, status='queued', reasons=tuple(r for r in current.reasons if r not in ('admin_pause', 'slice_deadline', 'admission_denied')))
                state['latest'], state['control'] = result, {'run_id': run_id, 'action': 'run'}
                self.store._write(state)
                self._authorized_run = current.run
                return self._ack(result)
        finally:
            owner.release()

    def authority_states(self):
        with self.store.lock():
            return self.store._read_metadata()['authorities']

    def reconcile_authority(self, scope, proof, replacement):
        """Separate trusted composition operation; no default auto-unpause."""
        with self._owner():
            with self.store.lock():
                state = self.store._read_metadata()
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
                stop=False
                with self.store.lock():
                    state = self.store._read_metadata()
                    current = state['latest']
                    if (current is None or self._authorized_run != current.run or not state['control']
                            or state['control']['action'] != 'run' or current.status in ('complete', 'failed')):
                        stop=True
                    else:
                        self._valid_run(current.run)
                        now = self.clock_ns()
                        if now < started:
                            raise _error('clock_regression')
                        if now - started >= 900 * 10**9:
                            result = replace(current, status='partial', reasons=tuple(dict.fromkeys((*current.reasons, 'slice_deadline'))))
                            state['latest'], state['control']['action'] = result, 'pause'
                            self.store._write(state)
                            stop=True
                if stop:
                    return self.store.load()
                # NO store/priority/shared-budget lock is held across discovery.
                loaded=self.store.load()
                if loaded is None or loaded.run!=current.run:
                    raise _error('run_owner_changed')
                try:
                    result = self._progress(discover(self.provider, current.run, loaded.cursor))
                except LeapsDataError as error:
                    result = replace(loaded, status='failed', reasons=tuple(dict.fromkeys((*current.reasons, error.code))))
                prepared=self.store.prepare(result)
                with self.store.lock():
                    state = self.store._read_metadata()
                    if state['latest'].run != current.run:
                        raise _error('run_owner_changed')
                    if state['control']['action'] == 'pause' and result.status not in ('complete', 'failed'):
                        result = replace(result, status='paused', reasons=tuple(dict.fromkeys((*result.reasons, 'admin_pause'))))
                        prepared=replace(prepared,status=result.status,reasons=result.reasons)
                    state['latest'] = prepared
                    for record in state['authorities'].values():
                        if record['reason'] and record['run_id'] == current.run.run_id:
                            record['cursor'] = 'blob:'+prepared.blob
                    if result.status == 'complete':
                        state['last_complete'] = prepared
                    if result.status != 'running':
                        state['control']['action'] = 'pause'
                    self.store._write(state)
                if result.status != 'running':
                    return result
        finally:
            owner.release()
