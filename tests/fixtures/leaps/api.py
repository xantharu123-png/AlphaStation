"""Synthetic LOCAL budget topology for bootstrap and HTTP/worker tests."""
from modules.atomic_cache import write_json_atomic
from modules.leaps.admission import Admission, FileBudget, BudgetProof, PriorityGate, PriorityProof, OSLease, private_namespace, lock_identity
from tests.fixtures.leaps.provider import NOW


def certified(root):
    folder = private_namespace(root)
    for name in ('synthetic-shared.lock','priority.lock'):
        lease = OSLease(folder/name)
        assert lease.acquire()
        lease.release()
    state = folder/'synthetic-shared.json'
    write_json_atomic(state, {'window':NOW//(60*10**9),'count':0})
    write_json_atomic(folder/'priority.json', {'observed_ns':NOW,'owners':{'stock':False,'bi':False,'alert':False}})
    budget = FileBudget(root,folder/'synthetic-shared.lock',state,BudgetProof(200,NOW-1,NOW+10**12,
        ('synthetic-allocation',),lock_identity(folder/'synthetic-shared.lock'),str(state.resolve())))
    priority = PriorityGate(root,PriorityProof(NOW-1,NOW+10**12,('synthetic-full-topology',),
        ('stock','bi','alert'),lock_identity(folder/'priority.lock')))
    Admission.provision(root,write_json_atomic)
    return Admission(budget,priority,lambda:NOW,atomic_writer=write_json_atomic)


class RoutedTransport:
    def __init__(self, before=None, stock_status=200):
        self.calls, self.before, self.stock_status = [], before, stock_status

    def market_headers(self):
        return {'Authorization':'Bearer synthetic-private-key'}

    def request(self,method,url,**kwargs):
        from urllib.parse import urlsplit,parse_qs
        from tests.fixtures.leaps.provider import Response,raw_contract,listing,chain_raw
        self.calls.append(url)
        if self.before:
            self.before(url)
        path = urlsplit(url).path
        if path == '/v3/reference/options/contracts':
            items = [raw_contract()]
        elif path == '/v3/reference/tickers':
            items = [listing()] if parse_qs(urlsplit(url).query)['type']==['CS'] else []
        elif path.startswith('/v2/last/nbbo/'):
            return Response({'status':'OK','results':{'T':'SOFI','p':20,'P':21,'s':200,'S':300,'y':NOW-1}},status=self.stock_status)
        elif path == '/v3/snapshot/options/SOFI':
            items = [chain_raw()]
        else:
            raise AssertionError('unexpected synthetic request')
        return Response({'status':'OK','results':items})
