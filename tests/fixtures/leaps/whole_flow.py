"""Synthetic wire responses for whole-flow tests; no provider or private data."""
from copy import deepcopy
from urllib.parse import urlsplit, parse_qs

from tests.fixtures.leaps.provider import NOW, Response, raw_contract, listing, chain_raw


class WholeFlowTransport:
    def __init__(self, *, stock=None, chain_pages=None, references=None, before=None):
        self.stock = dict(T='SOFI', p=20, P=21, s=200, S=300, y=NOW-1) if stock is None else stock
        self.chain_pages = chain_pages if chain_pages is not None else [[chain_raw()]]
        self.references = references if references is not None else [raw_contract()]
        self.before, self.calls = before, []

    def market_headers(self):
        return {'Authorization': 'Bearer synthetic-whole-flow'}

    def request(self, method, url, *, headers, timeout, allow_redirects):
        assert method == 'GET' and timeout == (3, 10) and allow_redirects is False
        self.calls.append(url)
        if self.before:
            self.before(url)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.path == '/v3/reference/options/contracts':
            page=int(query.get('cursor',['0'])[0])
            items=self.references[page*1000:(page+1)*1000]
            payload={'status':'OK','results':items,'request_id':'reference-wire-'+str(page+1)}
            if (page+1)*1000<len(self.references):
                payload['next_url']='https://api.massive.com'+parts.path+'?cursor='+str(page+1)
            return Response(payload)
        elif parts.path == '/v3/reference/tickers':
            kind = query['type'][0]
            items, identity = ([listing()] if kind == 'CS' else []), 'listing-wire-'+kind
        elif parts.path == '/v2/last/nbbo/SOFI':
            return Response({'status': 'OK', 'results': self.stock, 'request_id': 'stock-wire-1'})
        elif parts.path == '/v3/snapshot/options/SOFI':
            page = int(query.get('cursor', ['0'])[0])
            items = self.chain_pages[page]
            if isinstance(items, Exception):
                raise items
            payload = {'status': 'OK', 'results': deepcopy(items), 'request_id': 'chain-wire-'+str(page)}
            if page + 1 < len(self.chain_pages):
                payload['next_url'] = 'https://api.massive.com'+parts.path+'?cursor='+str(page+1)
            return Response(payload)
        else:
            raise AssertionError('Unexpected synthetic path '+parts.path)
        return Response({'status': 'OK', 'results': items, 'request_id': identity})
