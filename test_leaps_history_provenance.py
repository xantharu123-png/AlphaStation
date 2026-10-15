"""Adjustment metadata must survive actual history acquisition into HV."""
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo

import pytest

from modules.leaps.provider import MassiveLeapsProvider
from modules.leaps.underlying import CachedUnderlyingLoader
from modules.signal_tracker import _is_us_equity_session
from tests.fixtures.leaps.discovery import completion
from tests.fixtures.leaps.provider import NOW, Admission, Response, Transport, capability, listing


@pytest.mark.parametrize('adjusted,expected', [(True, D(0)), (False, None), (None, None)])
def test_adjusted_history_envelope_reaches_hv_without_inventing_provenance(adjusted, expected):
    days=[]
    current=date(2026,10,12)
    while len(days)<253:
        if _is_us_equity_session(current):
            days.append(current)
        current-=timedelta(days=1)
    bars=[dict(t=int(datetime.combine(day,datetime.min.time(),tzinfo=ZoneInfo('America/New_York')).timestamp())*1000,
               o=20,h=21,l=19,c=20,v=1000) for day in reversed(days)]
    envelope=dict(status='OK',ticker='SOFI',results=bars)
    if adjusted is not None:
        envelope['adjusted']=adjusted
    admission=Admission()
    transport=Transport([Response(envelope)],admission)
    source=MassiveLeapsProvider(transport,lambda:NOW,admission,capability())
    loader=CachedUnderlyingLoader(source,completion_producer=completion)
    evidence=loader.load('SOFI',listing(),now_ns=NOW)
    assert len(transport.calls)==1
    assert evidence.indicators['hv_252_decimal']==expected
    if adjusted is True:
        assert evidence.indicators['hv_meta']['adjustment_basis']=='split_adjusted'
        assert evidence.indicators['hv_meta']['return_count']==252


def test_snapshot_daily_volume_is_not_open_interest_or_fabricated_timestamp():
    from tests.fixtures.leaps.provider import chain_raw,contract
    admission=Admission()
    source=MassiveLeapsProvider(Transport([],admission),lambda:NOW,admission,capability())
    result=source.parse_chain_item(chain_raw(),contract())
    assert result.volume==123
    assert result.open_interest==1500
    assert result.volume_source_ns==NOW
    assert result.volume_session=='2026-10-13'
    raw=chain_raw()
    raw.pop('day')
    missing=source.parse_chain_item(raw,contract())
    assert missing.volume is None and missing.volume_source_ns is None and missing.volume_session is None


def test_adjusted_history_provenance_survives_sec_retry_checkpoint():
    from modules.leaps.underlying import UnderlyingLoadPaused
    admission=Admission()
    transport=Transport([Response({'status':'OK','ticker':'SOFI','adjusted':True,'results':[
        {'t':1791777600000,'o':20,'h':21,'l':19,'c':20,'v':1000}]}),
        Response(status=429),Response({'cik':123,'facts':{}})],admission)
    clock=[NOW]
    source=MassiveLeapsProvider(transport,lambda:clock[0],admission,capability())
    loader=CachedUnderlyingLoader(source,completion_producer=completion,
                                  configured_sec_user_agent='Synthetic synthetic@example.invalid')
    with pytest.raises(UnderlyingLoadPaused) as caught:
        loader.load('SOFI',listing(cik='123'),now_ns=clock[0])
    assert len(transport.calls)==2
    assert len(caught.value.progress['sources'])==2, caught.value.progress['reasons']
    clock[0]+=16*10**9
    result=loader.load('SOFI',listing(cik='123'),now_ns=clock[0],checkpoint=caught.value.progress)
    assert len(transport.calls)==3
    assert result.indicators['indicators_meta']['completed_history_bars']==1
    assert result.sources[1]['adjusted'] is True
