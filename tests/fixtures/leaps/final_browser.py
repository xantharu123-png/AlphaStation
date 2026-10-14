"""Synthetic public-contract export from real discovery, store and API service.

No browser-shaped monetary fixtures: all prices/scenarios/status flags below
are produced by Python production code. Not market, entitlement or license data.
"""
from datetime import date, datetime, timedelta
from dataclasses import replace
from decimal import Decimal as D
from zoneinfo import ZoneInfo

from modules.atomic_cache import write_json_atomic
from modules.leaps.api_contract import LeapsApiService, parse_scan
from modules.leaps.models import QuoteObservation, LeapsSnapshot
from modules.leaps.runtime import LeapsStore
from modules.leaps.service import DiscoveryProvider, discover
from modules.leaps.underlying import CachedUnderlyingLoader
from tests.fixtures.leaps.provider import NOW, run
from tests.fixtures.leaps.discovery import Market, term_producer, session, completion


def facts(financial=False):
    periods=[('2023-01-01','2023-12-31','2024-02-15'),('2024-01-01','2024-12-31','2025-02-15'),('2025-01-01','2025-12-31','2026-02-15')]
    gaap={}
    for name,values,duration,unit in (
        ('RevenueFromContractWithCustomerExcludingAssessedTax',[100,125,160],True,'USD'),
        ('NetIncomeLoss',[12,17,24],True,'USD'),('NetCashProvidedByUsedInOperatingActivities',[18,24,32],True,'USD'),
        ('PaymentsToAcquirePropertyPlantAndEquipment',[3,4,5],True,'USD'),('StockholdersEquity',[75,90,110],False,'USD'),
        ('LongTermDebt',[25,22,20],False,'USD'),('CashAndCashEquivalentsAtCarryingValue',[8,14,24],False,'USD'),
        ('WeightedAverageNumberOfDilutedSharesOutstanding',[10,10,10],True,'shares'),
        ('EntityCommonStockSharesOutstanding',[10,10,10],False,'shares')):
        gaap[name]={'units':{unit:[dict(end=end,filed=filed,form='10-K',val=value,**({'start':start} if duration else {})) for (start,end,filed),value in zip(periods,values)]}}
    if financial:
        gaap['LoansAndLeasesReceivableNetReportedAmount']={'units':{'USD':[{'end':'2025-12-31','filed':'2026-02-15','form':'10-K','val':310}]}}
    return {'cik':1,'entityName':'Synthetic Evidence Company','facts':{'us-gaap':gaap}}


class BrowserMarket(Market):
    def __init__(self,kind):
        super().__init__()
        self.kind=kind
        self.listings['CS'][0].update(name='Synthetic Verified Company',cik='1',sic_description='Banks' if kind=='bank' else '')
    def daily_history(self,ticker,**kwargs):
        bars=[]
        if self.kind!='bank':
            for i in range(220):
                day=date(2026,10,12)-timedelta(days=219-i)
                price=D(10)+D(i)/100
                bars.append({'t':int(datetime.combine(day,datetime.min.time(),ZoneInfo('America/New_York')).timestamp())*1000,
                             'o':price,'h':price+1,'l':price-1,'c':price,'v':1000})
        return {'ticker':ticker,'results':bars}
    def company_facts(self,*args,**kwargs): return facts(self.kind=='bank')
    def underlying_quote(self,ticker):
        return QuoteObservation() if self.kind=='missing' else super().underlying_quote(ticker)


def export(root):
    cases={}
    for kind in ('supported','bank','missing','excluded'):
        folder=root/kind; folder.mkdir()
        market=BrowserMarket(kind)
        provider=DiscoveryProvider(market,terms_producer=term_producer,session_producer=session,
            underlying_loader=CachedUnderlyingLoader(market,completion_producer=completion,configured_sec_user_agent='Synthetic fixture'))
        filters={'close_above_ema200':True,'min_underlying_price':D(30 if kind=='excluded' else 10)}
        spec=replace(run(),run_id='fixture-'+kind,filters=filters)
        cursor=None
        for _ in range(20):
            snapshot=discover(provider,spec,cursor)
            if snapshot.status!='running': break
            cursor=snapshot.cursor
        assert snapshot.status=='complete'
        store=LeapsStore(folder,write_json_atomic,clock_ns=lambda:NOW)
        store.publish(snapshot)
        backend=LeapsApiService(store,clock_ns=lambda:NOW)
        results=backend.results(limit=20)
        scenarios={}
        for quantity in (1,3):
            scenarios[str(quantity)]=backend.scenarios(dict(run_id=results['run_id'],snapshot_id=results['snapshot_id'],contract_id=results['rows'][0]['contract']['id'],quantity=quantity))
        cases[kind]={'results':results,'status':backend.status(),'scenarios':scenarios}
    folder=root/'recovery';folder.mkdir()
    store=LeapsStore(folder,write_json_atomic,clock_ns=lambda:NOW)
    backend=LeapsApiService(store,clock_ns=lambda:NOW)
    recovery={}
    for key,status,phase,day in [('terminal_partial','partial','done',run().market_date),
                                ('soft_partial','partial','chain',run().market_date),
                                ('stale_paused','paused','chain',run().market_date-timedelta(days=1))]:
        store.publish(LeapsSnapshot(replace(run(),run_id='recovery-'+key,market_date=day),status,{},
                      {'progress':{'phase':phase,'completed':1,'total':2}},(),None,()))
        recovery[key]=backend.status()
    parsed=parse_scan({'filters':{'max_be_rise_percent':'25.001','min_maturity_months':'15','max_maturity_months':'18',
                     'min_underlying_price':'10.125','close_above_ema200':True}},NOW)
    store.publish(LeapsSnapshot(parsed,'queued',{}, {'progress':{'phase':'queued','completed':0,'total':None}},(),None,()))
    return {'fixture_boundary':'synthetic inputs -> production discovery/store/API DTO; not live data',
            'cases':cases,'recovery':recovery,'filtered_status':backend.status()}
