"""Completed daily quality in a worker, with injected evidence and admitted IO.

The pure builder trusts the caller's completed-bar provenance, but validates
EVERY causal bar before the native float analytics adapter. The loader obtains
that provenance only from an injected verified source-completion producer.
"""
from collections.abc import Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
import re
import math
from zoneinfo import ZoneInfo

from .contracts import parse_decimal, parse_ns_epoch, decimal_context
from .models import LeapsDataError, UnderlyingEvidence, _freeze
from .provider import DailyCompletionProof, _verify_daily_completion


def _plain(value):
    if isinstance(value, Mapping):
        return {key:_plain(item) for key,item in value.items()}
    if isinstance(value, (tuple,list)):
        return [_plain(item) for item in value]
    return value


def _finite_analytics(value, invalid):
    if isinstance(value,Mapping):
        return {key:_finite_analytics(item,invalid) for key,item in value.items()}
    if isinstance(value,(list,tuple)):
        return [_finite_analytics(item,invalid) for item in value]
    if (isinstance(value,float) and not math.isfinite(value)) or (isinstance(value,Decimal) and not value.is_finite()):
        invalid.append(True)
        return None
    return value


def _validated_facts(companyfacts):
    """Exclude malformed SEC unit values before the legacy numeric adapter.

    Company totals are NOT option prices: no 1e12 wire-price limit is applied.
    A copied envelope preserves the original source and legitimate large totals.
    """
    data=_plain(companyfacts) if isinstance(companyfacts,Mapping) else {}
    invalid=False
    taxonomies=data.get('facts',{})
    if not isinstance(taxonomies,dict):
        return {},True
    for taxonomy in taxonomies.values():
        if not isinstance(taxonomy,dict):
            continue
        for fact in taxonomy.values():
            if not isinstance(fact,dict) or not isinstance(fact.get('units'),dict):
                continue
            for unit,entries in fact['units'].items():
                if not isinstance(entries,list):
                    continue
                accepted=[]
                for entry in entries:
                    value=entry.get('val') if isinstance(entry,dict) else None
                    valid=type(value) in (int,float) or isinstance(value,Decimal)
                    try:
                        valid=valid and math.isfinite(float(value))
                    except (ValueError,OverflowError,TypeError):
                        valid=False
                    if valid:
                        accepted.append(entry)
                    else:
                        invalid=True
                fact['units'][unit]=accepted
    return data,invalid


def _bars(values, as_of):
    validated, previous = [], None
    for bar in values:
        day = date.fromisoformat(bar['date'])
        if day > as_of or (previous is not None and day <= previous):
            raise ValueError('invalid_history')
        if bar.get('completed') is not True:
            raise ValueError('uncompleted_history')
        numbers=[]
        for key in ('open','high','low','close','volume'):
            value=bar[key]
            if type(value) is not int and not isinstance(value,Decimal):
                raise ValueError('invalid_ohlcv')
            numbers.append(parse_decimal(value))
        opening,high,low,close,volume=numbers
        if min(opening,high,low,close)<=0 or volume<0 or not low<=min(opening,close)<=max(opening,close)<=high:
            raise ValueError('invalid_ohlcv')
        validated.append(dict(date=day.isoformat(),completed=True,**dict(zip(('open','high','low','close','volume'),numbers))))
        previous=day
    # Conversion happens only AFTER validating the entire causal series.
    return [{key:float(value) if isinstance(value,Decimal) else value for key,value in bar.items()} for bar in validated]


def build_underlying_evidence(ticker, *, completed_bars, facts, sources, as_of):
    """No network. Facts envelope contains companyfacts and optional valuation inputs."""
    from modules.ticker_detail_indicators import build_ticker_detail_indicators
    from modules.business_quality import analyze_company_facts

    reasons=[]
    indicators={}
    history='missing'
    if completed_bars:
        try:
            native=_bars(completed_bars,as_of)
            indicators=build_ticker_detail_indicators(native)
            # Preserve exact closed prices before the native float adapter.
            # Same SMA-seeded EMA definition, local Decimal50 for predicate
            # boundaries; never compare the latest quote with an older EMA.
            indicators['completed_close']=parse_decimal(completed_bars[-1]['close'])
            indicators['completed_close_session']=completed_bars[-1]['date']
            indicators['ema200_decimal']=None
            if len(completed_bars)>=200:
                with decimal_context():
                    closes=[parse_decimal(bar['close']) for bar in completed_bars]
                    ema=sum(closes[:200],Decimal(0))/200
                    for close in closes[200:]:
                        ema+=(close-ema)*Decimal(2)/201
                    indicators['ema200_decimal']=ema
            indicators['filter_ema_method']='completed_daily_sma_seed_decimal50'
            history=indicators['indicators_meta']['status']
            invalid=[]
            indicators=_finite_analytics(indicators,invalid)
            if invalid:
                reasons.append('invalid_native_analytics')
                history='partial'
                meta=indicators['indicators_meta']
                meta['status']='partial'
                for key,value in indicators.items():
                    if key!='indicators_meta' and value is None:
                        meta['available'][key]=False
                        meta['unavailable_reasons'][key]='invalid_native_analytics'
        except (KeyError,TypeError,ValueError,ArithmeticError):
            history='invalid'
            reasons.append('invalid_completed_history')
    else:
        reasons.append('missing_completed_history')
    envelope=facts if isinstance(facts,Mapping) else {}
    companyfacts,invalid_values=_validated_facts(envelope.get('companyfacts'))
    analysis=analyze_company_facts(companyfacts,
        price=envelope.get('price'),market_cap=envelope.get('market_cap'),
        shares_outstanding=envelope.get('shares_outstanding'),industry=envelope.get('industry',''))
    invalid_facts=[]
    analysis=_finite_analytics(analysis,invalid_facts)
    if invalid_facts:
        analysis['status']='partial'
        reasons.append('invalid_native_facts_analytics')
    if invalid_values:
        analysis['status']='partial'
        reasons.append('invalid_company_fact_values')
    # Explicitly current wall-clock filing analysis, even with a historical chart.
    analysis['freshness_basis']='analysis_wall_clock_utc'
    analysis['backtest_eligible']=False
    facts_status='complete' if analysis.get('status')=='ok' else 'partial'
    if analysis.get('profile') in ('financial','reit') or analysis.get('specialist_data_required'):
        facts_status='partial'
    if facts_status!='complete':
        reasons.append('partial_company_facts')
    coverage=dict(history=history,facts=facts_status,quality='complete' if history=='complete' and facts_status=='complete' else 'partial',reasons=tuple(reasons))
    return UnderlyingEvidence(ticker,indicators,analysis,coverage,tuple(sources),as_of)


class UnderlyingLoadPaused(LeapsDataError):
    """Transient refusal plus immutable acquired evidence for worker persistence."""
    def __init__(self,code,authority,progress):
        super().__init__(code,'provider')
        self.authority=authority
        self.progress=_freeze(progress)


def _restore_progress(checkpoint,ticker,listing,as_of,now_ns):
    try:
        expected={'ticker','listing','proof','history_done','bars','sources','reasons','failures'}
        if not isinstance(checkpoint,Mapping) or set(checkpoint)!=expected:
            raise ValueError()
        progress=_plain(checkpoint)
        if progress['ticker']!=ticker or progress['listing']!=_plain(listing) or type(progress['history_done']) is not bool:
            raise ValueError()
        for key in ('bars','sources','reasons','failures'):
            if not isinstance(progress[key],list):
                raise ValueError()
        proof=progress['proof']
        if proof is not None:
            if not isinstance(proof,DailyCompletionProof) or proof.market_date>as_of:
                raise ValueError()
            _verify_daily_completion(proof,ticker,proof.market_date,now_ns)
        if not progress['history_done']:
            if proof is None or any(progress[key] for key in ('bars','sources','reasons','failures')):
                raise ValueError()
        if progress['bars']:
            if proof is None or progress['sources']!=[proof]:
                raise ValueError()
            _bars(progress['bars'],proof.market_date)
        if progress['sources'] and progress['sources']!=[proof]:
            raise ValueError()
        if any(not isinstance(reason,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,63}',reason) for reason in progress['reasons']):
            raise ValueError()
        for failure in progress['failures']:
            if not isinstance(failure,dict) or set(failure)!={'authority','reason'} or failure['authority']!='market' or failure['reason'] not in progress['reasons']:
                raise ValueError()
        return progress
    except (KeyError,TypeError,ValueError,ArithmeticError):
        raise LeapsDataError('invalid_underlying_checkpoint','underlying') from None


class CachedUnderlyingLoader:
    """Run-scoped cache. All acquisitions use the supplied admitted provider.

    completion_producer(ticker, as_of, now_ns) -> DailyCompletionProof|None.
    No CIK lookup, environment/secret access or calendar assumptions occur.
    Instantiate per run; cache keys bind listing and source proof, not options.
    """
    def __init__(self, provider, *, completion_producer=None, configured_sec_user_agent=None):
        self.provider=provider
        self.completion_producer=completion_producer
        self.user_agent=configured_sec_user_agent
        self._cache={}
        self._facts={}

    def load(self,ticker,listing,*,now_ns,checkpoint=None):
        now_ns=parse_ns_epoch(now_ns)
        as_of=datetime.fromtimestamp(now_ns//10**9,timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
        key=(ticker,as_of,tuple(sorted((str(k),str(v)) for k,v in listing.items())))
        progress=_restore_progress(checkpoint,ticker,listing,as_of,now_ns) if checkpoint is not None else None
        if key in self._cache:
            return self._cache[key]
        if progress is None:
            proof=self.completion_producer(ticker,as_of,now_ns) if self.completion_producer else None
            progress=dict(ticker=ticker,listing=_plain(listing),proof=proof,history_done=False,
                          bars=[],sources=[],reasons=[],failures=[])
        proof=progress['proof']
        bars=progress['bars']; sources=progress['sources']; reasons=progress['reasons']; failures=progress['failures']
        facts=None
        if not progress['history_done'] and proof is None:
            reasons.append('unverified_daily_completion')
            progress['history_done']=True
        elif not progress['history_done']:
            try:
                if not isinstance(proof,DailyCompletionProof):
                    raise LeapsDataError('unverified_daily_completion','underlying')
                _verify_daily_completion(proof,ticker,proof.market_date,now_ns)
                data=self.provider.daily_history(ticker,completed_through=proof.market_date,as_of=as_of,completion=proof)
                if (not isinstance(data,Mapping) or data.get('ticker')!=ticker or data.get('next_url') is not None
                        or not isinstance(data.get('results',()),(tuple,list))):
                    raise LeapsDataError('invalid_history_envelope','underlying')
                previous=None
                for raw in data.get('results',()):
                    if not isinstance(raw,Mapping):
                        raise LeapsDataError('invalid_completed_history','underlying')
                    timestamp=raw.get('t')
                    if type(timestamp) is not int or not 10**12<=timestamp<10**13:
                        raise LeapsDataError('invalid_completed_history','underlying')
                    day=datetime.fromtimestamp(timestamp//1000,timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
                    if day>proof.market_date or (previous is not None and day<=previous):
                        raise LeapsDataError('invalid_completed_history','underlying')
                    if day==proof.market_date and timestamp*10**6!=proof.window_start_ns:
                        raise LeapsDataError('invalid_completed_history','underlying')
                    bars.append(dict(date=day.isoformat(),completed=True,**{k:raw[v] for k,v in zip(('open','high','low','close','volume'),('o','h','l','c','v'))}))
                    previous=day
                _bars(bars,as_of)  # Validate entire causal OHLCV before facts price use.
                sources.append(proof)
            except LeapsDataError as error:
                if error.code in ('admission_denied','rate_limited'):
                    raise UnderlyingLoadPaused(error.code,'market',progress) from None
                bars.clear(); reasons.append(error.code)
                failures.append({'authority':'market','reason':error.code})
                if error.code=='unverified_daily_completion':
                    progress['proof']=None
            except (KeyError,TypeError,ValueError,OverflowError):
                bars.clear(); reasons.append('invalid_completed_history')
            progress['history_done']=True
        cik=listing.get('cik')
        verified_listing=(listing.get('ticker')==ticker and listing.get('market')=='stocks'
                          and listing.get('locale')=='us' and listing.get('active') is True
                          and listing.get('type') in ('CS','ADRC','ADRP'))
        if not verified_listing or not isinstance(cik,str) or not re.fullmatch(r'[0-9]{1,10}',cik):
            reasons.append('unverified_listing_cik')
        elif not self.user_agent:
            reasons.append('sec_user_agent_required')
        else:
            try:
                if cik not in self._facts:
                    raw=self.provider.company_facts(cik,configured_sec_user_agent=self.user_agent)
                    if (not isinstance(raw,Mapping) or type(raw.get('cik')) is not int or raw['cik']!=int(cik)
                            or not isinstance(raw.get('facts'),Mapping)):
                        raise LeapsDataError('invalid_company_facts','underlying')
                    self._facts[cik]=raw
                facts=dict(companyfacts=self._facts[cik],industry=listing.get('sic_description',''),
                           price=bars[-1]['close'] if bars else None)
                sources.append({'source':'sec_companyfacts','cik':cik,'basis':'live_current_not_pit'})
            except LeapsDataError as error:
                # Local/shared admission refusal pauses all LEAPS acquisitions;
                # SEC HTTP429 is authority-local and must not stop market chains.
                if error.code=='admission_denied':
                    raise UnderlyingLoadPaused(error.code,'sec',progress) from None
                reasons.append(error.code)
                failures.append({'authority':'sec','reason':error.code})
        result=build_underlying_evidence(ticker,completed_bars=bars,facts=facts,sources=sources,as_of=as_of)
        coverage=dict(result.coverage,reasons=tuple(dict.fromkeys((*result.coverage['reasons'],*reasons))),provider_failures=tuple(failures))
        result=UnderlyingEvidence(ticker,result.indicators,result.facts,coverage,result.sources,as_of)
        self._cache[key]=result
        return result
