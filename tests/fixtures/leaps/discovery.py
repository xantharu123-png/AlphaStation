"""Synthetic paginated discovery source and explicit proof factories."""
from copy import deepcopy
from datetime import date
from modules.leaps.models import PageResult, QuoteObservation, LeapsDataError
from modules.leaps.provider import MassiveLeapsProvider, VerifiedSession, DailyCompletionProof
from tests.fixtures.leaps.provider import NOW, D, Admission, Transport, capability, raw_contract, listing, chain_raw, terms


class Market:
    def __init__(self, tickers=('SOFI',), pages=1):
        self.references = [[raw_contract(t)] for t in tickers]
        self.references.extend([] for _ in range(max(0,pages-len(tickers))))
        self.listings = {kind:[] for kind in ('CS','ADRC','ADRP')}
        for i,t in enumerate(tickers):
            self.listings[('CS','ADRC','ADRP')[i%3]].append(listing(t, ('CS','ADRC','ADRP')[i%3]))
        self.chains = {}
        for t in tickers:
            raw = chain_raw()
            raw['details']['ticker'] = raw_contract(t)['ticker']
            raw['underlying_asset']['ticker'] = t
            self.chains[t] = [[raw]]
        self.calls = []
        self.clock_ns = lambda: NOW
        self.parser = MassiveLeapsProvider(Transport([],Admission()),lambda:NOW,Admission(),capability())

    def _page(self, kind, pages, cursor):
        self.calls.append((kind,cursor))
        index = int(cursor or 0)
        item = pages[index]
        if isinstance(item, Exception):
            raise item
        nxt = str(index+1) if index+1<len(pages) else None
        return PageResult(tuple(deepcopy(item)),nxt,nxt is None,None,'synthetic-page')

    def list_contract_page(self, run, cursor=None):
        return self._page('reference',self.references,cursor)

    def list_listing_page(self, kind, run, cursor=None):
        return self._page(kind,[self.listings[kind]],cursor)

    def chain_page(self, ticker, run, cursor=None):
        return self._page('chain:'+ticker,self.chains[ticker],cursor)

    def underlying_quote(self, ticker):
        self.calls.append(('stock',ticker))
        return QuoteObservation(D(20),D('20.1'),200,300,NOW-1,NOW,'shares','realtime',D(0),'synthetic-stock')

    def parse_chain_item(self, raw, contract):
        return self.parser.parse_chain_item(raw,contract)


def session(contract, stock, now_ns):
    return VerifiedSession(contract.provider_symbol,contract.underlying,contract.underlying,
                           NOW-10**12,NOW+10**12,NOW-10**12,NOW+10**12,('synthetic-session',))


def term_producer(raw, listing, run):
    return terms(listing['type'])


def completion(ticker,as_of,now):
    return DailyCompletionProof(ticker,date(2026,10,12),'massive_stock_daily_aggregates','day',1,
        1791777600000000000,1791849600000000000,1791849600000000000,1791849600000000000,D(0),('synthetic-full-source-window',))
