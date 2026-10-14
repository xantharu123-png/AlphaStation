"""LEAPS-only access, public projections and worker dispatch. No API imports/IO.

Policy and backend factories are trusted composition, never browser JSON. Source
capability evidence does not grant display rights. Cached source clocks are not
renewed by publication/read time. All synchronous methods run off the API loop.
"""
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .contracts import parse_decimal, parse_quantity
from .models import CapabilityEvidence, LeapsDataError, QuoteAssessment, RunSpec, UnderlyingObservation, ExpiryResult, to_json_value
from .pricing import evaluate_expiry, scenario_prices
from .provider import OptionQuote
from .runtime import _encode


class LeapsApiError(ValueError):
    def __init__(self, status, reason):
        self.status, self.reason = status, reason
        super().__init__(reason)


@dataclass(frozen=True)
class DisplayRights:
    """Out-of-band verified redistribution license for this policy's audience."""
    valid_from_ns: int
    valid_until_ns: int
    evidence_ids: tuple[str, ...]
    resources: tuple[str, ...]

    def valid(self, now):
        return (type(self.valid_from_ns) is int and type(self.valid_until_ns) is int
                and 0 < self.valid_from_ns <= now <= self.valid_until_ns
                and bool(self.evidence_ids) and all(isinstance(e, str) and e.strip() for e in self.evidence_ids)
                and {'options','stocks','derived','underlyings','terms'} <= set(self.resources))


@dataclass(frozen=True)
class LeapsAccessPolicy:
    enabled: bool = False
    admin: bool = False
    results_allowed: bool = False
    endpoint_capabilities: Mapping = field(default_factory=dict)
    capabilities: CapabilityEvidence | None = None
    display_rights: DisplayRights | None = None

    def require(self, endpoint, now):
        if endpoint in ('scan','control') and self.admin is not True:
            raise LeapsApiError(403, 'admin_required')
        if self.enabled is not True:
            raise LeapsApiError(403, 'feature_disabled')
        if self.endpoint_capabilities.get(endpoint) is not True:
            raise LeapsApiError(403, 'endpoint_capability_unverified')
        if endpoint in ('results','status','scenarios'):
            if self.results_allowed is not True:
                raise LeapsApiError(403, 'results_permission_required')
            if not isinstance(self.display_rights, DisplayRights) or not self.display_rights.valid(now):
                raise LeapsApiError(403, 'display_rights_unverified')
        if endpoint == 'scan':
            cap = self.capabilities
            if not isinstance(cap, CapabilityEvidence) or not all(_proven(getattr(cap, name)) for name in
                    ('reference','option_quotes','standard_terms','sessions','budget')):
                raise LeapsApiError(403, 'source_capability_unverified')

    def public(self, now):
        cap = self.capabilities
        return {'enabled':self.enabled is True, 'admin':self.admin is True,
                'results_allowed':self.results_allowed is True,
                'endpoint_capabilities':{name:self.endpoint_capabilities.get(name) is True for name in
                    ('results','status','scenarios','scan','control')},
                'capabilities':{name:_proven(getattr(cap,name)) if isinstance(cap,CapabilityEvidence) else False for name in
                    ('reference','option_quotes','stock_quotes','standard_terms','sessions','budget')},
                'display_rights_verified':isinstance(self.display_rights,DisplayRights) and self.display_rights.valid(now)}


def _proven(value):
    return (isinstance(value,Mapping) and value.get('accessible') is True
            and isinstance(value.get('evidence_ids'),(list,tuple)) and bool(value['evidence_ids'])
            and all(isinstance(e,str) and e.strip() for e in value['evidence_ids']))


def _identifier(value):
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9:|_\-.]{1,128}',value):
        raise LeapsApiError(422,'invalid_identifier')
    return value


def _object(body, required, allowed):
    if not isinstance(body,dict) or not required <= body.keys() or body.keys()-allowed:
        raise LeapsApiError(422,'invalid_schema')


def parse_scenario(body):
    _object(body, {'run_id','snapshot_id','contract_id','quantity'}, {'run_id','snapshot_id','contract_id','quantity','terminal_prices'})
    result = {key:_identifier(body[key]) for key in ('run_id','snapshot_id','contract_id')}
    try:
        result['quantity'] = parse_quantity(body['quantity'])
        if 'terminal_prices' in body:
            prices = body['terminal_prices']
            if not isinstance(prices,list) or not 1 <= len(prices) <= 20 or any(not isinstance(p,str) for p in prices):
                raise LeapsApiError(422,'invalid_terminal_prices')
            result['terminal_prices'] = tuple(parse_decimal(p) for p in prices)
            if any(p < 0 for p in result['terminal_prices']):
                raise LeapsApiError(422,'invalid_terminal_prices')
    except LeapsDataError:
        raise LeapsApiError(422,'invalid_scenario') from None
    return result


def parse_scan(body, now):
    _object(body, set(), {'run_id','filters'})
    filters = body.get('filters',{})
    if not isinstance(filters,dict) or any(not isinstance(v,str) for v in filters.values()):
        raise LeapsApiError(422,'invalid_filters')
    try:
        parsed = {key:parse_decimal(value) for key,value in filters.items()}
        day = datetime.fromtimestamp(now // 10**9,timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
        run = RunSpec(_identifier(body.get('run_id',uuid4().hex)),day,parsed,'full-universe','1','1')
        from .service import _filters
        _filters(run)
        return run
    except LeapsDataError:
        raise LeapsApiError(422,'invalid_filters') from None


def parse_control(body):
    _object(body,{'run_id','action'},{'run_id','action'})
    if body['action'] not in ('pause','resume'):
        raise LeapsApiError(422,'invalid_control')
    return _identifier(body['run_id']), body['action']


def snapshot_identity(snapshot):
    # Includes original stored data/Ask/terms; never hashes freshly expired UI
    # metadata or the global envelope revision. RunA survives unrelated RunB.
    content = {'run':snapshot.run,'status':snapshot.status,'data':snapshot.data,
               'coverage':snapshot.coverage,'reasons':snapshot.reasons,'sources':snapshot.sources}
    return sha256(json.dumps(_encode(content),sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()


def _current_assessment(row, now):
    if not isinstance(row,Mapping) or not isinstance(row.get('observation'),OptionQuote) or not isinstance(row.get('assessment'),QuoteAssessment):
        raise LeapsApiError(503,'invalid_store')
    if row.get('contract') != row['observation'].contract or row.get('terms_fingerprint') != row['contract'].terms_fingerprint:
        raise LeapsApiError(503,'invalid_store')
    # The tagged store legitimately permits native analytics floats, but they
    # must never cross the monetary/scenario boundary as trusted money.
    def money(value):
        return value is None or (isinstance(value,Decimal) and value.is_finite())
    if not all(money(row.get(key)) for key in ('break_even','be_rise','spread','ask_to_bid_loss')):
        raise LeapsApiError(503,'invalid_store')
    reference = row.get('stock_reference')
    if not isinstance(reference,UnderlyingObservation) or not money(reference.price):
        raise LeapsApiError(503,'invalid_store')
    contract = row['contract']
    if not isinstance(contract.strike,Decimal) or not isinstance(contract.premium_multiplier,Decimal):
        raise LeapsApiError(503,'invalid_store')
    for scenario in row.get('scenarios',()):
        if (not isinstance(scenario,Mapping) or not money(scenario.get('terminal'))
                or not isinstance(scenario.get('result'),ExpiryResult)
                or not all(money(value) for value in vars(scenario['result']).values())):
            raise LeapsApiError(503,'invalid_store')
    assessment = row['assessment']
    source = row['observation'].quote.source_ns
    valid = (assessment.comparison_eligible is True and assessment.expiry_ns is not None
             and now <= assessment.expiry_ns and source is not None and source <= now)
    reasons = assessment.reasons if valid or not assessment.comparison_eligible else (*assessment.reasons,'cached_comparison_expired')
    return replace(assessment,comparison_eligible=valid,reasons=tuple(reasons))


def _public_row(row, now):
    result = dict(row,assessment=_current_assessment(row,now))
    result['comparison_scope_quantity'] = 1
    return to_json_value(result)


class LeapsApiService:
    def __init__(self, store, *, runner=None, clock_ns=time.time_ns):
        self.store, self.runner, self.clock_ns = store, runner, clock_ns
        if runner is not None and runner.store is not store:
            raise LeapsApiError(503,'unbound_runner_store')
        self._worker = None
        self._lock = threading.Lock()
        self._last_start = None
        self._worker_error = None

    def _read(self):
        with self.store.lock():
            return self.store._read()

    def _select(self, state, *, view='complete', run_id=None):
        if view not in ('complete','latest'):
            raise LeapsApiError(422,'invalid_view')
        options = (state['last_complete'],state['latest']) if view == 'complete' else (state['latest'],)
        return next((s for s in options if s is not None and (run_id is None or s.run.run_id==run_id)),None)

    def results(self, *, limit=50, offset=0, view='complete', run_id=None):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=10**9:
            raise LeapsApiError(422,'invalid_page')
        state = self._read()
        snapshot = self._select(state,view=view,run_id=run_id)
        if snapshot is None:
            return {'run_id':None,'snapshot_id':None,'status':'empty','rows':[],'total':0,'next_offset':None}
        rows = snapshot.data.get('comparisons',())
        if not isinstance(rows,(tuple,list)):
            raise LeapsApiError(503,'invalid_store')
        total = len(rows)
        now = self.clock_ns()
        coverage = dict(snapshot.coverage)
        coverage['eligible'] = sum(r.get('filter_status')=='passed' and _current_assessment(r,now).comparison_eligible for r in rows)
        return {'run_id':snapshot.run.run_id,'snapshot_id':snapshot_identity(snapshot),'status':snapshot.status,
                'rows':[_public_row(r,now) for r in rows[offset:offset+limit]],'total':total,
                'next_offset':offset+limit if offset+limit<total else None,
                'coverage':to_json_value(coverage),'reasons':list(snapshot.reasons)}

    def status(self):
        state = self._read()
        snapshot = state['latest']
        return {'run_id':snapshot.run.run_id if snapshot else None,'status':snapshot.status if snapshot else 'empty',
                'progress':to_json_value(snapshot.coverage.get('progress')) if snapshot else None,
                'reasons':list(snapshot.reasons) if snapshot else [],
                'last_complete_run_id':state['last_complete'].run.run_id if state['last_complete'] else None,
                'worker_error':self._worker_error}

    def scenarios(self, body):
        state = self._read()
        snapshot = next((s for s in (state['latest'],state['last_complete']) if s is not None
                         and s.run.run_id==body['run_id'] and snapshot_identity(s)==body['snapshot_id']),None)
        if snapshot is None:
            raise LeapsApiError(409,'stale_snapshot')
        matches = [r for r in snapshot.data.get('comparisons',()) if r.get('contract') and r['contract'].id==body['contract_id']]
        if len(matches)!=1:
            raise LeapsApiError(409,'stored_contract_unavailable')
        row = matches[0]
        public = _public_row(row,self.clock_ns())
        if not row['assessment'].algebra_eligible:
            raise LeapsApiError(422,'no_valid_stored_ask')
        option, quantity = row['observation'], body['quantity']
        reference = row.get('stock_reference')
        prices = body.get('terminal_prices')
        if prices is None:
            prices = scenario_prices(reference.price if reference else None)
        scenarios = [{'terminal':terminal,'result':evaluate_expiry(option.contract,option.quote.ask,terminal,
                     quantity=quantity,underlying=reference,delta=option.greeks.delta)} for terminal in prices]
        result = to_json_value({'run_id':body['run_id'],'snapshot_id':body['snapshot_id'],'contract_id':body['contract_id'],
            'quantity':quantity,'hypothetical':True,'fees':'unknown','quantity_is_fill_evidence':False,
            'comparison_scope_quantity':1,
            'requested_size_liquidity':'insufficient' if option.quote.ask_size is not None and option.quote.ask_size<quantity else 'unverified',
            'option_depth_unit':'contracts','stock_depth_unit':'shares','stock_reference_kind':reference.price_kind if reference else None,
            'stock_reference_is_executable_buy':False,'quote_source_ns':option.quote.source_ns,'scenarios':scenarios})
        result['assessment'] = public['assessment']
        return result

    def _dispatch(self):
        with self._lock:
            if self._worker and self._worker.is_alive():
                return
            def work():
                try:
                    self.runner.run_slice()
                except Exception:
                    self._worker_error = 'worker_failed'
            self._worker_error = None
            self._worker = threading.Thread(target=work,name='leaps-worker',daemon=True)
            self._worker.start()

    def _claim(self):
        # One LEAPS throttle for scan AND resume, not legacy manual middleware.
        now = self.clock_ns()
        with self._lock:
            if self._last_start is not None and now-self._last_start < 60*10**9:
                raise LeapsApiError(429,'manual_cooldown')
            self._last_start = now

    def scan(self, run):
        if self.runner is None:
            raise LeapsApiError(503,'configured_runner_required')
        self._claim()
        snapshot = self.runner.start(run)
        self._dispatch()
        return {'run_id':snapshot.run.run_id,'status':snapshot.status}

    def control(self, run_id, action):
        if self.runner is None:
            raise LeapsApiError(503,'configured_runner_required')
        if action=='resume':
            self._claim()
            snapshot = self.runner.resume(run_id)
            self._dispatch()
        else:
            snapshot = self.runner.pause(run_id)
        return {'run_id':snapshot.run.run_id,'status':snapshot.status}
