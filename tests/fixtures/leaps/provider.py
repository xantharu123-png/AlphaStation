"""Synthetic authentic-schema provider fixtures; no real credentials or IO."""
from datetime import date
from decimal import Decimal as D
import json

from modules.leaps.models import AdmissionDecision, CapabilityEvidence, RunSpec
from modules.leaps.contracts import parse_standard_contract

NOW = 1791898200000000000


class Admission:
    def __init__(self, allowed=True):
        self.allowed, self.held, self.events = allowed, False, []

    def try_acquire(self, scope):
        self.events.append(('acquire', scope))
        if not self.allowed:
            return AdmissionDecision(False, 'priority_active', D(10))
        assert not self.held
        self.held = True
        return AdmissionDecision(True, 'allowed', None)

    def release(self, scope):
        assert self.held
        self.held = False
        self.events.append(('release', scope))


class Response:
    def __init__(self, data=None, status=200, text=None):
        self.status_code = status
        self.text = text if text is not None else json.dumps(data or {'status': 'OK', 'results': []}, default=lambda v: float(v) if isinstance(v,D) else None)


class Transport:
    def __init__(self, responses, admission):
        self.responses, self.admission, self.calls = list(responses), admission, []

    def market_headers(self):
        return {'Authorization': 'Bearer synthetic-private-key'}

    def request(self, method, url, *, headers, timeout, allow_redirects):
        assert self.admission.held
        self.calls.append((method, url, headers, timeout, allow_redirects))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def capability():
    return CapabilityEvidence(None, {'accessible':True, 'feed_mode':'realtime', 'feed_lag_s':D(0), 'evidence_ids':('synthetic-feed',)},
                              {'accessible':True, 'feed_mode':'realtime', 'feed_lag_s':D(0), 'evidence_ids':('synthetic-feed',)},
                              None, None, None, None, NOW, ('synthetic-capability',))


def run():
    return RunSpec('synthetic-run', date(2026,10,13), {}, 'v1', '1', '1')


def raw_contract(ticker='SOFI', strike=15):
    return dict(ticker=f'O:{ticker}280121C{strike*1000:08d}', underlying_ticker=ticker,
                contract_type='call', strike_price=strike, expiration_date='2028-01-21',
                shares_per_contract=100, exercise_style='american', primary_exchange='XNAS')


def listing(ticker='SOFI', kind='CS', **extra):
    return dict(ticker=ticker, market='stocks', locale='us', type=kind, active=True,
                currency_name='usd', primary_exchange='XNAS', **extra)


def terms(kind='CS'):
    return dict(standard=True, asset_class='equity' if kind == 'CS' else 'adr',
                premium_multiplier=100, currency='USD', settlement='physical', evidence_ids=['synthetic-terms'])


def contract():
    return parse_standard_contract(raw_contract(), listing(), terms())


def chain_raw():
    return dict(details=dict(ticker='O:SOFI280121C00015000', contract_type='call',exercise_style='american',
                             expiration_date='2028-01-21',shares_per_contract=100,strike_price=15),
                last_quote=dict(ask=D('5.2'),bid=D('5.1'),ask_size=10,bid_size=20,last_updated=NOW-1,midpoint=D('5.15'),timeframe='REAL-TIME'),
                greeks=dict(delta=D('0.6'),gamma=D('0.01'),theta=D('-0.005'),vega=D('0.3')),
                implied_volatility=D('0.4'),open_interest=1500,
                underlying_asset=dict(ticker='SOFI',price=D(20),last_updated=NOW,timeframe='REAL-TIME'),
                day=dict(open=D(5),high=D('5.5'),low=D('4.9'),close=D('5.1'),volume=123,last_updated=NOW),
                last_trade=dict(price=D('5.15'),size=1,sip_timestamp=NOW,timeframe='REAL-TIME'),
                fmv=D('5.15'),fmv_last_updated=NOW)
