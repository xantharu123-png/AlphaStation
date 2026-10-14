"""Checkpointable offline orchestration; call from a worker, never an API loop.

No producer is implicitly trusted/configured here. Terms, session and daily
completion discovery are injected by the deployment's separately verified
evidence layer. A checkpoint is trusted local runtime state, NOT a client token.
"""
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass, replace
from datetime import date
from decimal import Decimal
from hashlib import sha256
import json
import re

from .contracts import decimal_context, expiry_window, add_calendar_months, parse_decimal, parse_standard_contract
from .models import (LeapsDataError, LeapsSnapshot, OptionContract, QuoteObservation,
                     UnderlyingObservation, UnderlyingEvidence, GreeksObservation, to_json_value)
from .pricing import evaluate_expiry, scenario_prices, spread_metrics
from .provider import OptionQuote, StockQuote, VerifiedSession, DailyCompletionProof, assess_quote
from .underlying import CachedUnderlyingLoader, UnderlyingLoadPaused


@dataclass(frozen=True)
class ComparisonEvidence:
    underlying: UnderlyingEvidence
    stock: StockQuote
    session: VerifiedSession | None


class DiscoveryProvider:
    """Injected market boundary, cache loader and pure verified-proof factories.

    terms_producer(raw, listing, run) -> normative mapping|None
    session_producer(contract, stock:StockQuote, now_ns) -> VerifiedSession|None
    underlying_loader.load(ticker, listing, *, now_ns) -> UnderlyingEvidence
    Producers must do no hidden IO. Loader IO goes through admitted provider.
    """
    def __init__(self, market, *, terms_producer=None, session_producer=None, underlying_loader=None):
        self.market=market
        self.terms_producer=terms_producer
        self.session_producer=session_producer
        self.underlying_loader=underlying_loader or CachedUnderlyingLoader(market)
        self._comparison_run=None
        self._comparisons={}


_FILTERS={'max_be_rise','max_spread','min_open_interest','min_delta','max_delta',
          'min_maturity_months','max_maturity_months','min_underlying_price','max_underlying_price','close_above_ema200'}


def _filters(run):
    if set(run.filters)-_FILTERS:
        raise LeapsDataError('unsupported_filter','provider')
    if 'close_above_ema200' in run.filters and run.filters['close_above_ema200'] is not True:
        raise LeapsDataError('invalid_filter','provider')
    values={key:(True if key=='close_above_ema200' else parse_decimal(value)) for key,value in run.filters.items()}
    if any(value<0 for key,value in values.items() if key!='max_be_rise'):
        raise LeapsDataError('invalid_filter','provider')
    if any(values[key]>1 for key in ('min_delta','max_delta') if key in values):
        raise LeapsDataError('invalid_filter','provider')
    if values.get('min_delta',Decimal(0))>values.get('max_delta',Decimal(1)):
        raise LeapsDataError('invalid_filter','provider')
    for key in ('min_maturity_months','max_maturity_months'):
        if key in values and (values[key]!=values[key].to_integral_value() or not 12<=values[key]<=24):
            raise LeapsDataError('invalid_filter','provider')
    if values.get('min_maturity_months',12)>values.get('max_maturity_months',24):
        raise LeapsDataError('invalid_filter','provider')
    if ('min_open_interest' in values and values['min_open_interest']!=values['min_open_interest'].to_integral_value()
            or any(values[key]<=0 for key in ('min_underlying_price','max_underlying_price') if key in values)
            or values.get('min_underlying_price',0)>values.get('max_underlying_price',Decimal('1e12'))):
        raise LeapsDataError('invalid_filter','provider')
    return values


def _fingerprint(value):
    return sha256(json.dumps(to_json_value(value),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def compare_contract(contract, observation, evidence, run, now_ns):
    """Exact raw BE-rise rank basis; dated Ask algebra is never a buy signal."""
    filters=_filters(run)
    if observation.contract!=contract or evidence.underlying.ticker!=contract.underlying:
        raise LeapsDataError('comparison_identity_mismatch','contract')
    assessment=assess_quote(observation,evidence.stock,now_ns=now_ns,verified_session=evidence.session)
    quote=evidence.stock.quote
    # A dated, genuine one-sided bid is a reference, not an executable buy.
    # Missing/future original time or contradictory positive sides cannot anchor
    # relative scenarios/ranking. Receipt time never repairs source evidence.
    valid_source=quote.source_ns is not None and quote.source_ns<=now_ns
    crossed=quote.bid is not None and quote.ask is not None and quote.ask>0 and quote.bid>quote.ask
    price=quote.bid if valid_source and not crossed and quote.bid is not None and quote.bid>0 else None
    stock=UnderlyingObservation(evidence.stock.ticker,price,'bid' if price else None,quote.source_ns,quote.feed_mode)
    ask=observation.quote.ask
    be=rise=spread=loss=None
    scenarios=()
    if assessment.algebra_eligible:
        with decimal_context():
            be=contract.strike+ask
            rise=(be-price)/price if price else None
        if observation.quote.bid is None or 0<=observation.quote.bid<=ask:
            spread,loss=spread_metrics(observation.quote.bid,ask)
        scenarios=tuple({'terminal':terminal,'result':evaluate_expiry(contract,ask,terminal,underlying=stock,delta=observation.greeks.delta)} for terminal in scenario_prices(price))
    indicators=evidence.underlying.indicators
    meta=indicators.get('indicators_meta',{})
    close,ema=indicators.get('completed_close'),indicators.get('ema200_decimal')
    technical=None
    if (meta.get('history_valid') is True and meta.get('available',{}).get('ema200') is True
            and isinstance(close,Decimal) and close.is_finite() and close>0
            and isinstance(ema,Decimal) and ema.is_finite() and ema>0
            and indicators.get('completed_close_session')==meta.get('indicator_session')):
        try:
            day=date.fromisoformat(meta['indicator_session'])
            if day<=run.market_date:
                technical=close>ema
        except (ValueError,TypeError):
            pass
    metrics={'max_be_rise':rise,'max_spread':spread,'min_open_interest':observation.open_interest,
             'min_delta':observation.greeks.delta,'max_delta':observation.greeks.delta,
             'min_underlying_price':price,'max_underlying_price':price,'close_above_ema200':technical,
             'min_maturity_months':contract.expiry,'max_maturity_months':contract.expiry}
    missing=[]; rejected=[]
    for key,value in filters.items():
        metric=metrics[key]
        if key in ('min_maturity_months','max_maturity_months'):
            value=add_calendar_months(run.market_date,int(value))
        if metric is None:
            missing.append(key)
        elif key=='close_above_ema200' and not metric:
            rejected.append(key)
        elif (key.startswith('max_') and metric>value) or (key.startswith('min_') and metric<value):
            rejected.append(key)
    return LeapsSnapshot(run,'comparison',dict(contract=contract,terms_fingerprint=contract.terms_fingerprint,
        company_name=None,company_name_source=None,
        remaining_term={'basis_date':run.market_date,'days':(contract.expiry-run.market_date).days},
        observation=observation,assessment=assessment,stock_reference=stock,underlying=evidence.underlying,
        break_even=be,be_rise=rise,spread=spread,ask_to_bid_loss=loss,scenarios=scenarios,
        greeks=observation.greeks,open_interest=observation.open_interest,open_interest_basis=observation.open_interest_basis,
        filter_status='missing_evidence' if missing else 'excluded' if rejected else 'passed',
        filter_missing=tuple(missing),filter_rejected=tuple(rejected),filter_fingerprint=_fingerprint(run.filters)),{},(),None,()).data


# Checkpoint codec preserves wire Decimal versus string, exact ns and frozen DTOs.
# No arbitrary module loading, pickle, eval, or user-specified type construction.
_TYPES={kind.__name__:kind for kind in (OptionContract,QuoteObservation,UnderlyingEvidence,
    GreeksObservation,OptionQuote,StockQuote,VerifiedSession,DailyCompletionProof,ComparisonEvidence)}


def _encode(value):
    if isinstance(value,Decimal):
        return {'$':'decimal','v':str(value)}
    if type(value) is date:
        return {'$':'date','v':value.isoformat()}
    if is_dataclass(value):
        return {'$':type(value).__name__,'v':{field.name:_encode(getattr(value,field.name)) for field in fields(value)}}
    if isinstance(value,Mapping):
        return {'$':'map','v':{key:_encode(item) for key,item in value.items()}}
    if isinstance(value,(list,tuple)):
        return [_encode(item) for item in value]
    return value


def _decode(value):
    if isinstance(value,list):
        return [_decode(item) for item in value]
    if isinstance(value,dict):
        if set(value)!={'$','v'}:
            raise ValueError('invalid checkpoint tag')
        kind,body=value['$'],value['v']
        if kind=='decimal':
            # Exact storage is not external monetary admission. Rejected raw
            # observations and derived evidence must not poison later resumes.
            # Quote DTOs and contract parsing enforce monetary limits separately.
            if not isinstance(body,str):
                raise ValueError('checkpoint decimal')
            number=Decimal(body)
            if not number.is_finite():
                raise ValueError('checkpoint decimal')
            return number
        if kind=='date':
            return date.fromisoformat(body)
        if kind=='map':
            return {key:_decode(item) for key,item in body.items()}
        return _TYPES[kind](**{key:_decode(item) for key,item in body.items()})
    return value


def _save(state,run):
    payload=_encode(state)
    body=json.dumps(payload,sort_keys=True,separators=(',',':'),allow_nan=False)
    return json.dumps({'version':2,'run':_fingerprint(run),'digest':sha256(body.encode()).hexdigest(),'state':payload},separators=(',',':'))


def _load(checkpoint,run):
    try:
        raw=json.loads(checkpoint)
        if raw['version']!=2 or raw['run']!=_fingerprint(run):
            raise LeapsDataError('checkpoint_mismatch','provider')
        body=json.dumps(raw['state'],sort_keys=True,separators=(',',':'),allow_nan=False)
        if raw['digest']!=sha256(body.encode()).hexdigest():
            raise ValueError('checkpoint digest')
        state=_decode(raw['state'])
        if not isinstance(state,dict) or set(state)!=set(_initial()):
            raise ValueError('checkpoint fields')
        if state['phase'] not in ('reference','listings','intersection','underlying','chain','done','failed'):
            raise ValueError('checkpoint phase')
        for key in ('seen','references','listings','contracts','underlyings','underlying_progress','stocks','options','sessions','observed','exclusions','chain_anomalies'):
            if not isinstance(state[key],dict):
                raise ValueError('checkpoint collection')
        for key in ('reference_conflicts','listing_conflicts','listing_complete','tickers','chain_complete','chain_failed','reasons','sources'):
            if not isinstance(state[key],list):
                raise ValueError('checkpoint collection')
        if type(state['index']) is not int or not 0<=state['index']<=len(state['tickers']):
            raise ValueError('checkpoint index')
        if type(state['listing_kind']) is not int or not 0<=state['listing_kind']<=3:
            raise ValueError('checkpoint listing kind')
        if type(state['reference_complete']) is not bool or type(state['reference_duplicates']) is not int or state['reference_duplicates']<0:
            raise ValueError('checkpoint coverage')
        if state['cursor'] is not None and (not isinstance(state['cursor'],str) or not re.fullmatch(r'[A-Za-z0-9_\-=+/]{1,2048}',state['cursor'])):
            raise ValueError('checkpoint cursor')
        if state['listing_complete']!=['CS','ADRC','ADRP'][:state['listing_kind']]:
            raise ValueError('checkpoint listing coverage')
        if state['phase']=='listings' and state['listing_kind']==3:
            raise ValueError('checkpoint listing phase')
        if state['phase'] in ('underlying','chain') and state['index']>=len(state['tickers']):
            raise ValueError('checkpoint underlying phase')
        for key,kind in (('contracts',OptionContract),('underlyings',UnderlyingEvidence),('stocks',StockQuote),('options',OptionQuote)):
            if any(not isinstance(item,kind) for item in state[key].values()):
                raise ValueError('checkpoint data type')
        if any(not isinstance(item,Mapping) for key in ('references','listings','observed','exclusions') for item in state[key].values()):
            raise ValueError('checkpoint raw type')
        if len(set(state['tickers']))!=len(state['tickers']) or any(not isinstance(t,str) for t in state['tickers']):
            raise ValueError('checkpoint ticker identity')
        for symbol,option in state['options'].items():
            ticker=option.contract.underlying
            if (symbol not in state['contracts'] or option.contract!=state['contracts'][symbol]
                    or ticker not in state['underlyings'] or ticker not in state['stocks']):
                raise ValueError('checkpoint option binding')
        for ticker,pending in state['underlying_progress'].items():
            if (state['phase']!='underlying' or state['index']>=len(state['tickers'])
                    or ticker!=state['tickers'][state['index']] or ticker not in state['stocks']
                    or state['stocks'][ticker].ticker!=ticker or not isinstance(pending,Mapping)
                    or set(pending)!={'authority','reason','acquisition'}
                    or pending['authority'] not in ('market','sec')
                    or pending['reason'] not in ('admission_denied','rate_limited')
                    or not isinstance(pending['acquisition'],Mapping)):
                raise ValueError('checkpoint pending underlying')
        return state
    except LeapsDataError:
        raise
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):
        raise LeapsDataError('invalid_checkpoint','provider') from None


def _initial():
    return dict(phase='reference',cursor=None,seen={},references={},reference_conflicts=[],reference_duplicates=0,
        reference_complete=False,listings={},listing_conflicts=[],listing_kind=0,listing_complete=[],
        contracts={},tickers=[],index=0,underlyings={},underlying_progress={},stocks={},options={},sessions={},observed={},
        chain_complete=[],chain_failed=[],exclusions={},chain_anomalies={},reasons=[],sources=[])


def _exclude(state,key,reason):
    state['exclusions'][key]={'symbol':key,'reason':reason}
    state['options'].pop(key,None)


def _merge(state,items,kind):
    target=state['references'] if kind=='reference' else state['listings']
    conflicts=state['reference_conflicts'] if kind=='reference' else state['listing_conflicts']
    for item in items:
        key=item.get('ticker')
        if not isinstance(key,str) or not key:
            key='invalid:'+_fingerprint(item)
        if key in target:
            if target[key]!=item:
                if key not in conflicts:
                    conflicts.append(key)
            elif kind=='reference':
                state['reference_duplicates']+=1
        else:
            target[key]=item


def _page(state,key,acquire):
    seen=state['seen'].setdefault(key,[])
    cursor=state['cursor']
    if cursor is not None and cursor in seen:
        raise LeapsDataError('cursor_cycle','provider')
    page=acquire(cursor)
    if page.reason or page.complete!=(page.next_cursor is None):
        raise LeapsDataError(page.reason or 'invalid_page_completion','provider')
    if page.next_cursor is not None and (page.next_cursor==cursor or page.next_cursor in seen):
        raise LeapsDataError('cursor_cycle','provider')
    if cursor is not None:
        seen.append(cursor)
    state['cursor']=page.next_cursor
    if page.request_id:
        state['sources'].append({'scope':key,'request_id':page.request_id})
    return page


def _intersection(provider,state,run):
    lower,upper=expiry_window(run.market_date)
    identities={}
    for symbol,raw in state['references'].items():
        ticker=raw.get('underlying_ticker')
        listing=state['listings'].get(ticker) if isinstance(ticker,str) else None
        try:
            if symbol in state['reference_conflicts']:
                raise LeapsDataError('conflicting_reference','contract')
            if ticker in state['listing_conflicts']:
                raise LeapsDataError('conflicting_listing','contract')
            if not listing:
                raise LeapsDataError('missing_listing','contract')
            terms=provider.terms_producer(raw,listing,run) if provider.terms_producer else None
            if terms is None:
                raise LeapsDataError('unverified_terms','contract')
            contract=parse_standard_contract(raw,listing,terms)
            if not lower<=contract.expiry<=upper:
                raise LeapsDataError('outside_expiry_window','contract')
            if contract.id in identities and identities[contract.id]!=symbol:
                _exclude(state,identities[contract.id],'conflicting_contract_identity')
                state['contracts'].pop(identities[contract.id],None)
                raise LeapsDataError('conflicting_contract_identity','contract')
            identities[contract.id]=symbol
            state['contracts'][symbol]=contract
        except LeapsDataError as error:
            _exclude(state,symbol,error.code)
    state['tickers']=sorted({c.underlying for c in state['contracts'].values()})
    state['phase']='underlying' if state['tickers'] else 'done'


def _chain_end(state,ticker,complete):
    state['chain_complete' if complete else 'chain_failed'].append(ticker)
    for symbol,contract in state['contracts'].items():
        if contract.underlying==ticker and symbol not in state['observed']:
            _exclude(state,symbol,'not_observed_in_completed_chain' if complete else 'chain_incomplete')
    state['index']+=1
    state['cursor']=None
    state['phase']='underlying' if state['index']<len(state['tickers']) else 'done'


def _snapshot(state,provider,run,*,pause=None):
    now=provider.market.clock_ns()
    run_key=_fingerprint(run)
    if provider._comparison_run!=run_key:
        provider._comparison_run=run_key
        provider._comparisons={}
    evidence_keys={ticker:_fingerprint((state['underlyings'][ticker],stock))
                   for ticker,stock in state['stocks'].items() if ticker in state['underlyings']}
    rows=[]
    retained={}
    for symbol,option in state['options'].items():
        ticker=option.contract.underlying
        evidence=ComparisonEvidence(state['underlyings'][ticker],state['stocks'][ticker],state['sessions'].get(symbol))
        assessment=assess_quote(option,evidence.stock,now_ns=now,verified_session=evidence.session)
        key=(evidence_keys[ticker],_fingerprint((option,evidence.session)),
             assessment.algebra_eligible,
             evidence.stock.quote.source_ns is not None and evidence.stock.quote.source_ns<=now)
        cached=provider._comparisons.get(symbol)
        if cached is None or cached[0]!=key:
            row=compare_contract(option.contract,option,evidence,run,now)
        else:
            # Reuse economics only within the same original-source validity
            # boundaries. Current freshness/session assessment is never cached.
            row=dict(cached[1],assessment=assessment)
        name=state['listings'].get(ticker,{}).get('name')
        name=name.strip() if isinstance(name,str) and 0<len(name.strip())<=256 else None
        row=dict(row,company_name=name,company_name_source='verified_listing_reference' if name else None)
        retained[symbol]=(key,row)
        rows.append(row)
    provider._comparisons=retained
    rows.sort(key=lambda row:(row['be_rise'] is None,row['be_rise'] or Decimal(0),row['contract'].id))
    coverage=dict(reference_seen=len(state['references']),reference_total=len(state['references']) if state['reference_complete'] else None,
        reference_complete=state['reference_complete'],reference_duplicates=state['reference_duplicates'],
        listings_seen=len(state['listings']),listings_complete=tuple(state['listing_complete']),
        underlyings_total=len(state['tickers']) if len(state['listing_complete'])==3 and state['phase']!='intersection' else None,
        underlyings_loaded=len(state['underlyings']),chain_complete=len(state['chain_complete']),chain_failed=len(state['chain_failed']),
        chain_observed=len(state['observed']),parsed=len(rows),excluded=len(state['exclusions']),
        chain_unmatched=sum(reason=='unmatched_chain_contract' for reason in state['chain_anomalies'].values()),
        chain_malformed=sum(reason=='unidentified_chain_record' for reason in state['chain_anomalies'].values()),
        unobserved=sum(x['reason'] in ('not_observed_in_completed_chain','chain_incomplete') for x in state['exclusions'].values()),
        filter_unknown=sum(row['filter_status']=='missing_evidence' for row in rows),
        filter_excluded=sum(row['filter_status']=='excluded' for row in rows),
        eligible=sum(row['filter_status']=='passed' and row['assessment'].comparison_eligible for row in rows),
        quality_partial=sum(item.coverage.get('quality')!='complete' for item in state['underlyings'].values()))
    status=('paused' if pause else 'failed' if state['phase']=='failed' else
            ('partial' if state['reasons'] else 'complete') if state['phase']=='done' else 'running')
    return LeapsSnapshot(run,status,{'comparisons':tuple(rows),'underlyings':state['underlyings'],
        'pending_underlyings':{ticker:{'authority':item['authority'],'reason':item['reason']} for ticker,item in state['underlying_progress'].items()},
        'exclusions':tuple(state['exclusions'].values()),'chain_anomalies':state['chain_anomalies'],
        'filter_fingerprint':_fingerprint(run.filters)},
        coverage,tuple(dict.fromkeys((*state['reasons'],*((pause,) if pause else ())))),_save(state,run),tuple(state['sources']))


def discover(provider,run,checkpoint=None):
    """Perform one safe page/intersection/underlying unit and return saved state.

    Loop while status=running; persist cursor after each call. Paused means the
    same unit can be retried by an authorized worker without recounting pages.
    Complete is universe traversal completeness, NOT complete quality evidence.
    """
    # Check binding before filter validation to distinguish changed run replay.
    state=_load(checkpoint,run) if checkpoint is not None else _initial()
    _filters(run)
    phase=state['phase']
    try:
        if phase=='reference':
            page=_page(state,'reference',lambda c:provider.market.list_contract_page(run,c))
            _merge(state,page.items,'reference')
            if page.complete:
                state['reference_complete']=True
                state['phase']='listings'
        elif phase=='listings':
            kind=('CS','ADRC','ADRP')[state['listing_kind']]
            page=_page(state,kind,lambda c:provider.market.list_listing_page(kind,run,c))
            _merge(state,page.items,'listing')
            if page.complete:
                state['listing_complete'].append(kind)
                state['listing_kind']+=1
                if state['listing_kind']==3:
                    state['phase']='intersection'
        elif phase=='intersection':
            _intersection(provider,state,run)
        elif phase=='underlying':
            ticker=state['tickers'][state['index']]
            now=provider.market.clock_ns()
            # Acquire stock first so denied admission never repeats quality IO.
            if ticker not in state['stocks']:
                try:
                    state['stocks'][ticker]=StockQuote(ticker,provider.market.underlying_quote(ticker))
                except LeapsDataError as error:
                    if error.code in ('admission_denied','rate_limited'):
                        raise
                    state['reasons'].append(error.code)
                    state['stocks'][ticker]=StockQuote(ticker,QuoteObservation())
            loader=provider.underlying_loader
            try:
                if isinstance(loader,CachedUnderlyingLoader):
                    saved=state['underlying_progress'].get(ticker)
                    quality=loader.load(ticker,state['listings'][ticker],now_ns=now,checkpoint=saved['acquisition'] if saved else None)
                else:
                    quality=loader.load(ticker,state['listings'][ticker],now_ns=now)
            except UnderlyingLoadPaused as error:
                state['underlying_progress'][ticker]={'authority':error.authority,'reason':error.code,'acquisition':error.progress}
                raise
            if quality.ticker!=ticker:
                raise LeapsDataError('underlying_identity_mismatch','underlying')
            state['underlying_progress'].pop(ticker,None)
            state['underlyings'][ticker]=quality
            state['phase']='chain'
        elif phase=='chain':
            ticker=state['tickers'][state['index']]
            page=_page(state,'chain:'+ticker,lambda c:provider.market.chain_page(ticker,run,c))
            for raw in page.items:
                details=raw.get('details')
                if not isinstance(details,Mapping):
                    state['chain_anomalies'][ticker+':'+_fingerprint(raw)]='unidentified_chain_record'
                    continue
                symbol=details.get('ticker')
                if not isinstance(symbol,str):
                    state['chain_anomalies'][ticker+':'+_fingerprint(raw)]='unidentified_chain_record'
                    continue
                if symbol not in state['contracts'] or state['contracts'][symbol].underlying!=ticker:
                    state['chain_anomalies'][ticker+':'+symbol]='unmatched_chain_contract'
                    continue  # Chain-only records are never admitted secretly.
                previous=state['options'].get(symbol)
                if symbol in state['observed'] and symbol in state['exclusions']:
                    continue  # A proven conflict remains excluded on replay.
                try:
                    contract=state['contracts'][symbol]
                    observation=provider.market.parse_chain_item(raw,contract)
                    if previous is not None:
                        old,new=previous.quote,observation.quote
                        # Validate contract binding before considering chronology.
                        # Metadata-only changes and receipt time are not a new
                        # quote; original provider time is the sole ordering key.
                        if replace(new,received_ns=old.received_ns)==old:
                            continue
                        if old.source_ns is not None and new.source_ns is not None:
                            if new.source_ns<old.source_ns:
                                continue
                            if new.source_ns==old.source_ns:
                                raise LeapsDataError('conflicting_chain_observation','provider')
                        else:
                            raise LeapsDataError('conflicting_chain_observation','provider')
                    state['observed'][symbol]=raw
                    state['options'][symbol]=observation
                    state['sessions'][symbol]=(provider.session_producer(contract,state['stocks'][ticker],provider.market.clock_ns())
                                               if provider.session_producer else None)
                except LeapsDataError as error:
                    state['observed'].setdefault(symbol,raw)
                    _exclude(state,symbol,error.code)
            if page.complete:
                _chain_end(state,ticker,True)
    except LeapsDataError as error:
        if error.code=='invalid_underlying_checkpoint':
            raise
        if error.code in ('admission_denied','rate_limited'):
            return _snapshot(state,provider,run,pause=error.code)
        state['reasons'].append(error.code)
        if phase in ('chain','underlying'):
            _chain_end(state,state['tickers'][state['index']],False)
        else:
            state['phase']='failed'
    return _snapshot(state,provider,run)
