"""LEAPS-only access, public projections and worker dispatch. No API imports/IO.

Policy and backend factories are trusted composition, never browser JSON. Source
capability evidence does not grant display rights. Cached source clocks are not
renewed by publication/read time. All synchronous methods run off the API loop.
"""
from collections.abc import Mapping
from collections import deque
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import math
import re
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .contracts import parse_decimal, parse_quantity, decimal_context
from .models import CapabilityEvidence, LeapsDataError, QuoteAssessment, RunSpec, UnderlyingObservation, ExpiryResult, to_json_value
from .pricing import evaluate_expiry, scenario_prices
from .provider import OptionQuote, valid_premium
from .runtime import _encode, SUPPORTED_SCHEMA_VERSION, SUPPORTED_METHODOLOGY_VERSION


class LeapsApiError(ValueError):
    def __init__(self, status, reason, *, retry_after=None):
        self.status, self.reason = status, reason
        self.retry_after = retry_after
        super().__init__(reason)


class LeapsReadAdmission:
    """Bounded API-WORKER policy, NOT a deployment-wide/account budget claim.

    Authenticated account identities are supplied only by the server route.
    No evictions of live windows (which would reset an attacker's quota).
    A held heavy token follows actual work, independent of client lifetime.
    """
    def __init__(self, *, clock=time.monotonic, max_accounts=4096):
        self.clock=clock
        self.max_accounts=max_accounts
        self._accounts={}
        self._heavy=0
        self._lock=threading.Lock()

    def claim(self, identity, endpoint):
        if not isinstance(identity,str) or not identity or len(identity)>512:
            raise LeapsApiError(401,'login_required')
        heavy=endpoint in ('results','scenarios')
        key=sha256(identity.encode()).hexdigest()
        now=self.clock()
        with self._lock:
            for old,item in list(self._accounts.items()):
                if now-item['last']>=60 and not item['active']:
                    del self._accounts[old]
            if key not in self._accounts:
                if len(self._accounts)>=self.max_accounts:
                    raise LeapsApiError(429,'read_account_capacity',retry_after=60)
                self._accounts[key]={'heavy':deque(),'light':deque(),'last':now,'active':0}
            item=self._accounts[key]
            item['last']=now
            queue=item['heavy' if heavy else 'light']
            while queue and now-queue[0]>=60:
                queue.popleft()
            limit,burst=(30,6) if heavy else (120,20)
            recent=[t for t in queue if now-t<1]
            wait=max(60-(now-queue[0]) if len(queue)>=limit else 0,
                     1-(now-recent[0]) if len(recent)>=burst else 0)
            if wait>0:
                raise LeapsApiError(429,'read_rate_limited',retry_after=max(1,math.ceil(wait)))
            if heavy and self._heavy>=2:
                raise LeapsApiError(429,'heavy_read_busy',retry_after=1)
            queue.append(now)
            if heavy:
                self._heavy+=1
                item['active']+=1
        gate=self
        class Token:
            released=False
            def release(self):
                with gate._lock:
                    if not self.released:
                        self.released=True
                        if heavy:
                            gate._heavy-=1
                            item['active']-=1
        return Token()


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
    if not isinstance(filters,dict) or any((v is not True if k=='close_above_ema200' else not isinstance(v,str)) for k,v in filters.items()):
        raise LeapsApiError(422,'invalid_filters')
    try:
        parsed = {}
        for key,value in filters.items():
            target={'max_be_rise_percent':'max_be_rise','max_spread_percent':'max_spread',
                    'min_delta_percent':'min_delta','max_delta_percent':'max_delta'}.get(key,key)
            if target in parsed or (target!=key and target in filters):
                raise LeapsApiError(422,'invalid_filters')
            with decimal_context():
                parsed[target]=(True if key=='close_above_ema200' else
                                parse_decimal(value)/100 if target!=key else parse_decimal(value))
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


def _current_assessment(row, now, clock_regressed=False):
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
                or not all(money(value) for key,value in vars(scenario['result']).items() if key!='stock_share_count')
                or (scenario['result'].stock_share_count is not None and
                    (type(scenario['result'].stock_share_count) is not int or not 100<=scenario['result'].stock_share_count<=10**8))):
            raise LeapsApiError(503,'invalid_store')
    assessment = row['assessment']
    source = row['observation'].quote.source_ns
    stock_source = reference.source_ns
    valid = (not clock_regressed and assessment.comparison_eligible is True and assessment.expiry_ns is not None
             and now <= assessment.expiry_ns and source is not None and source <= now
             and stock_source is not None and stock_source <= now)
    reasons = assessment.reasons if valid or not assessment.comparison_eligible else (*assessment.reasons,'cached_comparison_expired')
    for label,original in (('option',source),('stock',stock_source)):
        if original is not None and original>now:
            reasons=tuple(dict.fromkeys((*reasons,label+'_future_source')))
    if clock_regressed:
        reasons=tuple(dict.fromkeys((*reasons,'clock_regression')))
    algebra=assessment.algebra_eligible is True and valid_premium(row['observation'].quote,row['observation'].quote.received_ns)
    return replace(assessment,algebra_eligible=algebra,comparison_eligible=valid and algebra,reasons=tuple(reasons))


def _public_row(row, now, clock_regressed=False):
    result = dict(row,assessment=_current_assessment(row,now,clock_regressed))
    if not result['assessment'].algebra_eligible:
        result.update(break_even=None,be_rise=None,spread=None,ask_to_bid_loss=None,scenarios=())
    result['comparison_scope_quantity'] = 1
    return to_json_value(result)


_SORT_KEYS = {
    'underlying':lambda row:row['contract'].underlying,
    'strike':lambda row:row['contract'].strike,
    'expiry':lambda row:row['contract'].expiry,
    # Original Task4 one-contract economics, not new browser cash arithmetic.
    'cost_per_contract':lambda row:row['scenarios'][0]['result'].debit if row.get('scenarios') else None,
    'break_even':lambda row:row.get('break_even'),
    'be_rise':lambda row:row.get('be_rise'),
    'spread':lambda row:row.get('spread'),
    'delta':lambda row:row['greeks'].delta,
    'open_interest':lambda row:row.get('open_interest'),
}


def _sort_rows(rows, sort_by, order):
    # Stable ascending contract-ID ties in BOTH directions. Missing is not zero:
    # split nulls before descending order, so unknown stays last on every page.
    stable = sorted(rows,key=lambda row:row['contract'].id)
    values = [(None if sort_by in ('cost_per_contract','break_even','be_rise','spread')
               and not valid_premium(row['observation'].quote,row['observation'].quote.received_ns)
               else _SORT_KEYS[sort_by](row),row) for row in stable]
    present = [(value,row) for value,row in values if value is not None]
    present.sort(key=lambda item:item[0],reverse=order=='desc')
    return [row for _,row in present]+[row for value,row in values if value is None]


class LeapsApiService:
    def __init__(self, store, *, runner=None, clock_ns=time.time_ns):
        self.store, self.runner, self.clock_ns = store, runner, clock_ns
        if runner is not None and runner.store is not store:
            raise LeapsApiError(503,'unbound_runner_store')
        self._worker = None
        self._worker_running = False
        self._dispatch_pending = False
        self._lock = threading.Lock()
        self._last_start = None
        self._worker_error = None
        self._derived = {}
        self._current_snapshot_ids = set()

    def _read(self):
        state=self.store.read()
        retained={id(state[key]) for key in ('latest','last_complete') if state[key] is not None}
        with self._lock:
            self._current_snapshot_ids=retained
            self._derived={key:value for key,value in self._derived.items() if key in retained}
        return state

    def _derived_for(self,snapshot):
        # Store verifies ACTUAL blob bytes before handing us an immutable object.
        # Keep at most current latest/complete; no history/universe-sized copies.
        with self._lock:
            if id(snapshot) not in self._current_snapshot_ids:
                return {'snapshot':snapshot,'orders':{}}
            return self._derived.setdefault(id(snapshot),{'snapshot':snapshot,'orders':{}})

    def _identity(self,snapshot):
        entry=self._derived_for(snapshot)
        if 'identity' not in entry:
            entry['identity']=snapshot_identity(snapshot)
        return entry['identity']

    def _select(self, state, *, view='complete', run_id=None):
        if view not in ('complete','latest'):
            raise LeapsApiError(422,'invalid_view')
        options = (state['last_complete'],state['latest']) if view == 'complete' else (state['latest'],)
        return next((s for s in options if s is not None and (run_id is None or s.run.run_id==run_id)),None)

    def results(self, *, limit=50, offset=0, view='complete', run_id=None, sort_by='be_rise', order='asc'):
        if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or not 0<=offset<=10**9:
            raise LeapsApiError(422,'invalid_page')
        sort_by = 'underlying' if sort_by=='ticker' else sort_by
        if not isinstance(sort_by,str) or sort_by not in _SORT_KEYS or order not in ('asc','desc'):
            raise LeapsApiError(422,'invalid_sort')
        state = self._read()
        snapshot = self._select(state,view=view,run_id=run_id)
        if snapshot is None:
            return {'run_id':None,'snapshot_id':None,'status':'empty','rows':[],'total':0,'next_offset':None,
                    'sort_by':sort_by,'order':order}
        rows = snapshot.data.get('comparisons',())
        if not isinstance(rows,(tuple,list)):
            raise LeapsApiError(503,'invalid_store')
        total = len(rows)
        now,regressed=self.store.observe_clock(self.clock_ns)
        coverage = dict(snapshot.coverage)
        coverage['eligible'] = sum(r.get('filter_status')=='passed' and _current_assessment(r,now,regressed).comparison_eligible for r in rows)
        entry=self._derived_for(snapshot)
        ordering=(sort_by,order)
        if ordering not in entry['orders']:
            entry['orders'][ordering]=_sort_rows(rows,sort_by,order)
        ordered=entry['orders'][ordering]
        return {'run_id':snapshot.run.run_id,'snapshot_id':self._identity(snapshot),'status':snapshot.status,
                'market_date':snapshot.run.market_date.isoformat(),'filters':to_json_value(snapshot.run.filters),
                'rows':[_public_row(r,now,regressed) for r in ordered[offset:offset+limit]],'total':total,
                'next_offset':offset+limit if offset+limit<total else None,
                'coverage':to_json_value(coverage),'reasons':list(snapshot.reasons),'sort_by':sort_by,'order':order}

    def status(self):
        state = self.store.read(metadata=True)
        snapshot = state['latest']
        phase=snapshot.coverage.get('progress',{}).get('phase') if snapshot else None
        terminal=bool(snapshot and (snapshot.status in ('complete','failed') or phase in ('done','failed')))
        today=datetime.fromtimestamp(self.clock_ns()//10**9,timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
        compatible=bool(snapshot and snapshot.run.market_date==today
                        and snapshot.run.schema_version==SUPPORTED_SCHEMA_VERSION
                        and snapshot.run.methodology_version==SUPPORTED_METHODOLOGY_VERSION)
        resumable=bool(compatible and not terminal and snapshot.status in ('paused','partial')
                       and phase in ('queued','reference','listings','intersection','underlying','chain')
                       and not state.get('authorities',{}).get('market',{}).get('reason'))
        return {'run_id':snapshot.run.run_id if snapshot else None,'status':snapshot.status if snapshot else 'empty',
                'terminal':terminal,'resumable':resumable,
                'restartable':snapshot is None or snapshot.status not in ('queued','running'),
                'market_date':snapshot.run.market_date.isoformat() if snapshot else None,
                'filters':to_json_value(snapshot.run.filters) if snapshot else {},
                'progress':to_json_value(snapshot.coverage.get('progress')) if snapshot else None,
                'reasons':list(snapshot.reasons) if snapshot else [],
                'last_complete_run_id':state['last_complete'].run.run_id if state['last_complete'] else None,
                'worker_error':self._worker_error}

    def scenarios(self, body):
        state = self._read()
        snapshot = next((s for s in (state['latest'],state['last_complete']) if s is not None
                         and s.run.run_id==body['run_id'] and self._identity(s)==body['snapshot_id']),None)
        if snapshot is None:
            raise LeapsApiError(409,'stale_snapshot')
        matches = [r for r in snapshot.data.get('comparisons',()) if r.get('contract') and r['contract'].id==body['contract_id']]
        if len(matches)!=1:
            raise LeapsApiError(409,'stored_contract_unavailable')
        row = matches[0]
        now,regressed=self.store.observe_clock(self.clock_ns)
        public = _public_row(row,now,regressed)
        if not public['assessment']['algebra_eligible']:
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
            self._dispatch_pending = True
            if self._worker_running:
                return
            self._worker_running = True
            def work():
                while True:
                    with self._lock:
                        self._dispatch_pending = False
                    try:
                        self.runner.run_slice()
                    except Exception:
                        self._worker_error = 'worker_failed'
                    # A native slice may have released its owner while this
                    # thread is still alive. Consume accepted dispatches before
                    # atomically becoming idle; never lose the exit handoff.
                    with self._lock:
                        if self._dispatch_pending:
                            continue
                        self._worker_running = False
                        return
            self._worker_error = None
            self._worker = threading.Thread(target=work,name='leaps-worker',daemon=True)
            try:
                self._worker.start()
            except Exception:
                self._worker_running = False
                self._dispatch_pending = False
                raise

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
