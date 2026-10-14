"""Strict, injected LEAPS HTTP boundary. No credentials or I/O at import time.

Transport contract: market_headers() -> private mapping; request('GET', url,
headers=..., timeout=(3,10), allow_redirects=False) -> status_code/text.
Transport must perform exactly ONE attempt and raise ConnectionError/TimeoutError
for retryable connection failures. It must not log headers or response bodies.
Admission owns the cross-process lease; provider-local serialization is not a
replacement. Every attempted request, including retries and SEC, acquires it.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import json
import re
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .contracts import decimal_context, expiry_window, parse_decimal, parse_ns_epoch
from .models import (CapabilityEvidence, GreeksObservation, LeapsDataError, OptionContract,
                     PageResult, QuoteAssessment, QuoteObservation, RunSpec)


@dataclass(frozen=True)
class OptionQuote:
    contract: OptionContract
    quote: QuoteObservation
    greeks: GreeksObservation
    open_interest: int | None
    open_interest_basis: str = 'previous_session'
    reference_source_ns: int | None = None


@dataclass(frozen=True)
class StockQuote:
    ticker: str
    quote: QuoteObservation


@dataclass(frozen=True)
class DailyCompletionProof:
    """Trusted producer evidence for a complete provider ET daily bucket.

    window_start is the aggregate bucket start; exclusive window_end is the
    end of ALL eligible source coverage, not merely the regular stock session.
    No schedule, completion or lag is inferred from quotes or receipt time.
    """
    ticker: str
    market_date: date
    source: str
    timespan: str
    multiplier: int
    window_start_ns: int
    window_end_ns: int
    completed_ns: int
    available_ns: int
    feed_lag_s: Decimal
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.evidence_ids, (list, tuple)):
            raise _error('unverified_daily_completion')
        object.__setattr__(self, 'evidence_ids', tuple(self.evidence_ids))
        for field in ('window_start_ns','window_end_ns','completed_ns','available_ns'):
            _wire_ns(getattr(self, field))


@dataclass(frozen=True)
class VerifiedSession:
    """Caller-verified class schedule, never inferred from a global RTH flag.

    Interval ends are inclusive instants; schedule producers must express the
    precise final tradable ns. The option class is the proven OCC symbol root.
    """
    option_symbol: str
    underlying_ticker: str
    option_class: str
    option_start_ns: int
    option_end_ns: int
    stock_start_ns: int
    stock_end_ns: int
    evidence_ids: tuple[str, ...]

    def __post_init__(self):
        if not isinstance(self.evidence_ids, (list, tuple)):
            raise _error('invalid_session_evidence')
        object.__setattr__(self, 'evidence_ids', tuple(self.evidence_ids))
        for field in ('option_start_ns', 'option_end_ns', 'stock_start_ns', 'stock_end_ns'):
            if type(getattr(self, field)) is not int:
                raise _error('invalid_session_time')
            parse_ns_epoch(getattr(self, field))


def _error(code):
    return LeapsDataError(code, 'provider')


def resource_authority(path, scope='market'):
    """403 entitlement class, distinct from shared budget/credential authority."""
    if scope == 'sec':
        return 'sec'
    if path.startswith('/v2/last/nbbo/'):
        return 'market:stock_quotes'
    if path.startswith('/v3/snapshot/options/'):
        return 'market:option_quotes'
    if path.startswith('/v3/reference/options/'):
        return 'market:option_reference'
    if path.startswith('/v3/reference/tickers'):
        return 'market:stock_reference'
    if path.startswith('/v2/aggs/ticker/'):
        return 'market:stock_history'
    return 'market'


def authority_error(code, authority):
    error = _error(code)
    error.authority = authority
    return error


def _ticker(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Z][A-Z0-9.\-]{0,15}', value):
        raise _error('invalid_ticker')
    return value


def _wire_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, Decimal)):
        raise _error('invalid_wire_number')
    return parse_decimal(value)


def _size(value):
    if value is not None and (type(value) is not int or value < 0 or value > 10**12):
        raise _error('invalid_quote_size')
    return value


def _wire_ns(value):
    if type(value) is not int:
        raise _error('invalid_ns_epoch')
    return parse_ns_epoch(value)


def _feed(evidence):
    if not isinstance(evidence, Mapping) or evidence.get('accessible') is not True or not _evidence_ids(evidence.get('evidence_ids')):
        return None, None
    mode, lag = evidence.get('feed_mode'), evidence.get('feed_lag_s')
    if not isinstance(lag, Decimal) or not lag.is_finite() or lag < 0:
        return None, None
    if (mode == 'realtime' and lag == 0) or (mode == 'delayed' and lag > 0):
        return mode, lag
    return None, None


def _evidence_ids(value):
    return isinstance(value, (list, tuple)) and bool(value) and all(isinstance(v, str) and v.strip() for v in value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON field')
        result[key] = value
    return result


def _eastern_date(source_ns):
    try:
        return datetime.fromtimestamp(source_ns // 10**9, timezone.utc).astimezone(ZoneInfo('America/New_York')).date()
    except ZoneInfoNotFoundError:
        raise _error('calendar_unavailable') from None


def _verify_daily_completion(proof, ticker, completed_through, now_ns):
    if (not isinstance(proof, DailyCompletionProof) or proof.ticker != ticker
            or type(proof.market_date) is not date or proof.market_date != completed_through
            or proof.source != 'massive_stock_daily_aggregates' or proof.timespan != 'day'
            or type(proof.multiplier) is not int or proof.multiplier != 1
            or not _evidence_ids(proof.evidence_ids)
            or not isinstance(proof.feed_lag_s, Decimal) or not proof.feed_lag_s.is_finite()
            or proof.feed_lag_s < 0
            or not proof.window_start_ns < proof.window_end_ns <= proof.completed_ns <= proof.available_ns <= now_ns
            or proof.window_start_ns % 10**6 != 0
            or _eastern_date(proof.window_start_ns) != completed_through
            or _eastern_date(proof.window_end_ns - 1) != completed_through):
        raise _error('unverified_daily_completion')
    with decimal_context():
        try:
            lag = parse_decimal(proof.feed_lag_s)
        except LeapsDataError:
            raise _error('unverified_daily_completion') from None
        if proof.available_ns - proof.completed_ns < lag * 10**9:
            raise _error('unverified_daily_completion')


class MassiveLeapsProvider:
    def __init__(self, transport, clock_ns, admission, capability):
        self.transport, self.clock_ns = transport, clock_ns
        self.admission, self.capability = admission, capability
        self._denied = set()
        self._paused = set()
        self._lock = Lock()
        self._seen = {}
        self.request_count = 0
        self._request_ceiling = None

    def _request(self, path, params=None, *, scope='market', user_agent=None):
        origin = 'https://api.massive.com' if scope == 'market' else 'https://data.sec.gov'
        url = origin + path + ('?' + urlencode(params) if params else '')
        with self._lock:
            resource = resource_authority(path, scope)
            if scope in self._denied:
                raise authority_error('authorization_denied', scope)
            if resource in self._denied:
                raise authority_error('authorization_denied', resource)
            if scope in self._paused:
                raise _error('rate_limited')
            for attempt in range(2):
                if self._request_ceiling is not None and self.request_count >= self._request_ceiling:
                    raise _error('probe_request_limit')
                decision = self.admission.try_acquire(scope)
                if decision.allowed is not True:
                    raise _error('admission_denied')
                retry = False
                try:
                    headers = dict(self.transport.market_headers()) if scope == 'market' else {'User-Agent': user_agent}
                    headers['Accept'] = 'application/json'
                    self.request_count += 1
                    try:
                        response = self.transport.request('GET', url, headers=headers, timeout=(3, 10), allow_redirects=False)
                    except (ConnectionError, TimeoutError):
                        retry = True
                    except Exception:
                        raise _error('transport_error') from None
                    if not retry:
                        status = response.status_code
                        if status in (401, 403):
                            authority = scope if status == 401 else resource
                            self._denied.add(authority)
                            raise authority_error('authorization_denied', authority)
                        if status == 429:
                            self._paused.add(scope)
                            raise _error('rate_limited')
                        if 300 <= status < 400:
                            raise _error('redirect_rejected')
                        if 500 <= status < 600:
                            retry = True
                        elif status != 200:
                            raise _error('http_error')
                        else:
                            try:
                                data = json.loads(response.text, parse_float=Decimal, object_pairs_hook=_unique_object,
                                                  parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                            except (ValueError, TypeError, RecursionError):
                                raise _error('invalid_json') from None
                            if not isinstance(data, dict):
                                raise _error('invalid_response')
                            if scope == 'market' and data.get('status') not in ('OK', 'DELAYED'):
                                raise _error('provider_status')
                            return data
                except LeapsDataError:
                    raise
                except Exception:
                    raise _error('transport_error') from None
                finally:
                    self.admission.release(scope)
                if retry and attempt == 1:
                    raise _error('transport_unavailable')

    @staticmethod
    def _cursor(value):
        if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_\-=+/]{1,2048}', value):
            raise _error('invalid_cursor')
        return value

    def _page(self, path, params, run, cursor):
        key = (run.run_id, path, tuple(sorted(params.items())))
        seen = self._seen.setdefault(key, set())
        if cursor is not None:
            cursor = self._cursor(cursor)
            if cursor in seen:
                raise _error('cursor_cycle')
            params = {'cursor': cursor}
        data = self._request(path, params)
        items = data.get('results', [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise _error('invalid_page')
        for item in items:
            if 'strike_price' in item:
                _wire_number(item['strike_price'])
        next_cursor = None
        if data.get('next_url') is not None:
            raw = data['next_url']
            if not isinstance(raw, str) or len(raw) > 4096 or any(ord(c) < 33 for c in raw):
                raise _error('invalid_cursor')
            try:
                parts = urlsplit(raw)
                query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
                query = [(key, value) for key, value in query if key.lower() != 'apikey']
            except ValueError:
                raise _error('invalid_cursor') from None
            if parts.scheme != 'https' or parts.netloc != 'api.massive.com' or parts.path != path or parts.fragment or len(query) != 1 or query[0][0] != 'cursor':
                raise _error('invalid_cursor')
            next_cursor = self._cursor(query[0][1])
            if next_cursor == cursor or next_cursor in seen:
                raise _error('cursor_cycle')
        if cursor is not None:
            seen.add(cursor)
        request_id = data.get('request_id')
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', request_id)):
            request_id = None
        return PageResult(tuple(items), next_cursor, next_cursor is None, None, request_id)

    def list_contract_page(self, run, cursor=None):
        lower, upper = expiry_window(run.market_date)
        return self._page('/v3/reference/options/contracts', {'contract_type':'call', 'expired':'false',
            'as_of':run.market_date.isoformat(), 'expiration_date.gte':lower.isoformat(),
            'expiration_date.lte':upper.isoformat(), 'sort':'ticker', 'order':'asc', 'limit':1000}, run, cursor)

    def list_listing_page(self, kind, run, cursor=None):
        if kind not in ('CS', 'ADRC', 'ADRP'):
            raise _error('invalid_listing_kind')
        return self._page('/v3/reference/tickers', {'market':'stocks','locale':'us','active':'true',
            'type':kind, 'date':run.market_date.isoformat(), 'sort':'ticker','order':'asc','limit':1000}, run, cursor)

    def chain_page(self, underlying, run, cursor=None):
        lower, upper = expiry_window(run.market_date)
        return self._page('/v3/snapshot/options/' + _ticker(underlying), {'contract_type':'call',
            'expiration_date.gte':lower.isoformat(), 'expiration_date.lte':upper.isoformat(),
            'sort':'ticker', 'order':'asc', 'limit':250}, run, cursor)

    def underlying_quote(self, ticker):
        ticker = _ticker(ticker)
        data = self._request('/v2/last/nbbo/' + ticker)
        raw = data.get('results')
        if not isinstance(raw, Mapping) or raw.get('T') != ticker:
            raise _error('invalid_stock_quote')
        source_ns = _wire_ns(raw['y']) if raw.get('y') is not None else None
        now = parse_ns_epoch(self.clock_ns())
        if source_ns is not None and source_ns > now:
            raise _error('future_source')
        mode, lag = _feed(self.capability.stock_quotes)
        return QuoteObservation(bid=None if raw.get('p') is None else _wire_number(raw['p']),
            ask=None if raw.get('P') is None else _wire_number(raw['P']), bid_size=_size(raw.get('s')),
            ask_size=_size(raw.get('S')), source_ns=source_ns, received_ns=now, size_unit='shares',
            feed_mode=mode, feed_lag_s=lag, source='massive_stock_nbbo')

    def parse_chain_item(self, raw, contract):
        """Late join with independently proven reference/listing terms.

        Reference and Greek model clocks are not present in the snapshot
        schema: they remain None. Raw IV/Greeks are not normalized without
        separate unit/model evidence. Snapshot price/trade/FMV never supply Ask.
        """
        if not isinstance(raw, Mapping) or not isinstance(contract, OptionContract):
            raise _error('chain_contract_mismatch')
        details = raw.get('details')
        if not isinstance(details, Mapping):
            raise _error('chain_contract_mismatch')
        expected = dict(ticker=contract.provider_symbol, contract_type=contract.right,
                        exercise_style=contract.exercise_style, expiration_date=contract.expiry.isoformat(),
                        shares_per_contract=contract.shares)
        if any(details.get(key) != value for key, value in expected.items()) or type(details.get('shares_per_contract')) is not int:
            raise _error('chain_contract_mismatch')
        if _wire_number(details.get('strike_price')) != contract.strike:
            raise _error('chain_contract_mismatch')
        now = parse_ns_epoch(self.clock_ns())
        quote = self._option_quote(raw.get('last_quote'), now)
        greeks = raw.get('greeks', {})
        if not isinstance(greeks, Mapping):
            raise _error('invalid_greeks')
        greek_raw = {key:_wire_number(value) for key,value in greeks.items() if key in ('delta','gamma','theta','vega')}
        if raw.get('implied_volatility') is not None:
            greek_raw['implied_volatility'] = _wire_number(raw['implied_volatility'])
        return OptionQuote(contract, quote, GreeksObservation(raw=greek_raw), _size(raw.get('open_interest')))

    def _option_quote(self, raw, now):
        if raw is None:
            return QuoteObservation(received_ns=now, size_unit='contracts', source='massive_option_snapshot')
        if not isinstance(raw, Mapping):
            raise _error('invalid_option_quote')
        source_ns = _wire_ns(raw['last_updated']) if raw.get('last_updated') is not None else None
        if source_ns is not None and source_ns > now:
            raise _error('future_source')
        mode, lag = _feed(self.capability.option_quotes)
        expected_timeframe = {'realtime':'REAL-TIME', 'delayed':'DELAYED'}.get(mode)
        if raw.get('timeframe') != expected_timeframe:
            mode, lag = None, None
        return QuoteObservation(bid=None if raw.get('bid') is None else _wire_number(raw['bid']),
            ask=None if raw.get('ask') is None else _wire_number(raw['ask']),
            bid_size=_size(raw.get('bid_size')), ask_size=_size(raw.get('ask_size')),
            source_ns=source_ns, received_ns=now, size_unit='contracts', feed_mode=mode,
            feed_lag_s=lag, source='massive_option_snapshot')

    def daily_history(self, ticker, *, completed_through, as_of, completion=None):
        ticker = _ticker(ticker)
        now_ns = _wire_ns(self.clock_ns())
        if type(as_of) is not date or as_of != _eastern_date(now_ns):
            raise _error('invalid_as_of')
        if type(completed_through) is not date or completed_through > as_of:
            raise _error('invalid_completed_date')
        _verify_daily_completion(completion, ticker, completed_through, now_ns)
        start = completed_through - timedelta(days=730)
        data = self._request(f'/v2/aggs/ticker/{ticker}/range/1/day/{start}/{completed_through}',
                             {'adjusted':'true','sort':'asc','limit':50000})
        if data.get('next_url') is not None:
            raise _error('history_truncated')
        if data.get('ticker') != ticker or not isinstance(data.get('results', []), list):
            raise _error('invalid_history')
        for bar in data.get('results', []):
            if not isinstance(bar, Mapping) or type(bar.get('t')) is not int or not 10**12 <= bar['t'] < 10**13:
                raise _error('invalid_ms_epoch')
            bar_date = _eastern_date(bar['t'] * 10**6)
            if (bar_date > completed_through or
                    (bar_date == completed_through and bar['t'] * 10**6 != completion.window_start_ns)):
                raise _error('incomplete_daily_bar')
            for key in ('o','h','l','c','v'):
                if _wire_number(bar.get(key)) < 0:
                    raise _error('invalid_history')
        return PageResult((data,), None, True, None, None).items[0]

    def company_facts(self, cik, *, configured_sec_user_agent):
        if not isinstance(cik, str) or not re.fullmatch(r'[0-9]{1,10}', cik):
            raise _error('invalid_cik')
        if not isinstance(configured_sec_user_agent, str) or not 5 <= len(configured_sec_user_agent) <= 256 or any(ord(c) < 32 for c in configured_sec_user_agent):
            raise _error('sec_user_agent_required')
        data = self._request(f'/api/xbrl/companyfacts/CIK{int(cik):010d}.json', scope='sec', user_agent=configured_sec_user_agent)
        if type(data.get('cik')) is not int or data['cik'] != int(cik) or not isinstance(data.get('facts'), Mapping):
            raise _error('invalid_company_facts')
        return PageResult((data,), None, True, None, None).items[0]


def _quote_deadline(quote, now_ns, label, reasons):
    if quote.source_ns is None:
        reasons.append(label + '_missing_source')
        return None
    if quote.source_ns > now_ns:
        reasons.append(label + '_future_source')
        return None
    lag = quote.feed_lag_s
    if (not isinstance(lag, Decimal) or not lag.is_finite() or lag < 0
            or quote.feed_mode not in ('realtime','delayed')
            or (quote.feed_mode == 'realtime' and lag != 0)
            or (quote.feed_mode == 'delayed' and lag <= 0)):
        reasons.append(label + '_unknown_feed')
        return None
    with decimal_context():
        expiry_ns = quote.source_ns + int((lag + 60) * 10**9)
    if now_ns > expiry_ns:
        reasons.append(label + '_stale')
    return expiry_ns


def _session_matches(session, option, stock, now_ns):
    if not isinstance(session, VerifiedSession) or not _evidence_ids(session.evidence_ids):
        return False
    match = re.fullmatch(r'(?:O:)?([A-Z][A-Z0-9.]{0,15})[0-9]{6}C[0-9]{8}', option.contract.provider_symbol)
    if (not match or session.option_class != match.group(1)
            or session.option_symbol != option.contract.provider_symbol
            or session.underlying_ticker != stock.ticker or stock.ticker != option.contract.underlying):
        return False
    return (option.quote.source_ns is not None and stock.quote.source_ns is not None
            and session.option_start_ns <= option.quote.source_ns <= now_ns <= session.option_end_ns
            and session.stock_start_ns <= stock.quote.source_ns <= now_ns <= session.stock_end_ns)


def assess_quote(option, underlying, *, now_ns, verified_session):
    """Dated Ask-only algebra is separate from current two-sided comparison.

    Adequate is bounded displayed-size evidence, never a promise of execution.
    The anomalous price relation is a warning, not guaranteed arbitrage.
    """
    now_ns = parse_ns_epoch(now_ns)
    if not isinstance(option, OptionQuote) or not isinstance(underlying, StockQuote):
        raise _error('invalid_assessment_input')
    if not isinstance(option.quote, QuoteObservation) or not isinstance(underlying.quote, QuoteObservation):
        raise _error('invalid_assessment_input')
    reasons = []
    oq, sq = option.quote, underlying.quote
    algebra = oq.ask is not None and oq.ask > 0 and (oq.source_ns is None or oq.source_ns <= now_ns)
    if not algebra:
        reasons.append('no_valid_ask')
    if underlying.ticker != option.contract.underlying:
        reasons.append('mismatched_underlying')
    deadlines = [_quote_deadline(q, now_ns, label, reasons) for q,label in ((oq,'option'),(sq,'stock'))]
    for quote, label, unit in ((oq,'option','contracts'),(sq,'stock','shares')):
        if quote.bid is None or quote.bid <= 0 or quote.ask is None or quote.ask <= 0:
            reasons.append(label + '_two_sided_quote_missing')
        elif quote.bid > quote.ask:
            reasons.append(label + '_crossed_quote')
        if quote.size_unit != unit or any(type(size) is not int or size <= 0 or size > 10**12 for size in (quote.bid_size,quote.ask_size)):
            reasons.append(label + '_size_evidence_insufficient')
    if oq.source_ns is not None and sq.source_ns is not None and abs(oq.source_ns - sq.source_ns) > 30*10**9:
        reasons.append('source_time_skew')
    session_verified = _session_matches(verified_session, option, underlying, now_ns)
    if not session_verified:
        reasons.append('unverified_session')
    if all(reason.endswith('_size_evidence_insufficient') for reason in reasons):
        with decimal_context():
            if oq.ask + option.contract.strike < sq.bid:
                reasons.append('suspicious_price_relation')
    expiry_ns = (min(*deadlines, verified_session.option_end_ns, verified_session.stock_end_ns)
                 if session_verified and all(value is not None for value in deadlines) else None)
    return QuoteAssessment(algebra, not reasons, 'adequate' if not reasons else 'insufficient', tuple(reasons), expiry_ns)


def probe_capabilities(provider, *, max_requests=3):
    """Bounded technical access check; never establishes display/licensing rights.

    The ceiling counts physical attempts, including retries. Uses SOFI only as
    an authorized probe fixture, never as a universe whitelist.
    """
    if type(max_requests) is not int or not 1 <= max_requests <= 3:
        raise _error('invalid_probe_limit')
    if not isinstance(provider, MassiveLeapsProvider) or provider._request_ceiling is not None:
        raise _error('invalid_probe_provider')
    captured = parse_ns_epoch(provider.clock_ns())
    market_date = datetime.fromtimestamp(captured // 10**9, timezone.utc).date()
    run = RunSpec('capability-probe', market_date, {}, 'probe', '1', '1')
    results = [None, None, None]
    provider._request_ceiling = provider.request_count + max_requests
    try:
        for index in range(3):
            try:
                if index == 0:
                    provider._page('/v3/reference/options/contracts',
                        {'underlying_ticker':'SOFI', 'contract_type':'call', 'expired':'false', 'limit':1}, run, None)
                    results[index] = True
                elif index == 1:
                    page = provider.chain_page('SOFI', run)
                    quote_now = parse_ns_epoch(provider.clock_ns())
                    quotes = [provider._option_quote(item.get('last_quote'), quote_now) for item in page.items]
                    if any(quote.ask is not None and quote.bid is not None and quote.source_ns is not None for quote in quotes):
                        results[index] = True
                else:
                    quote = provider.underlying_quote('SOFI')
                    if quote.bid is not None and quote.ask is not None and quote.source_ns is not None:
                        results[index] = True
            except LeapsDataError as error:
                if error.code == 'authorization_denied':
                    if getattr(error, 'authority', 'market') == 'market':
                        results = [False, False, False]
                        break
                    results[index] = False
                if error.code in ('probe_request_limit', 'admission_denied', 'rate_limited'):
                    break
    finally:
        provider._request_ceiling = None
    return CapabilityEvidence(*results, None, None, None, None, captured, ('technical_probe_only',))
