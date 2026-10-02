"""Research source and directional diagnostics must not manufacture evidence."""
from datetime import datetime, timezone
import pytest
from scripts.scanner_history_audit import validate_ohlcv, unique_bars
from scripts.scanner_history_stock import directional_markouts, summarize_markouts


def bar(**changes):
    return dict(t=datetime(2026,7,2,tzinfo=timezone.utc).timestamp()*1000,
                o=100.,h=101.,l=99.,c=100.,v=1000.,**changes)


@pytest.mark.parametrize("timestamp", [True, 0, 12345, 1782950400000.5])
def test_market_collection_requires_real_integral_milliseconds(timestamp):
    row = bar()
    row["t"] = timestamp
    with pytest.raises(ValueError):
        validate_ohlcv(row)


@pytest.mark.parametrize("field,value", [("v",True),("v",float("inf")),("c",500),("l",102)])
def test_market_collection_rejects_bad_closed_observations(field,value):
    row = bar()
    row[field] = value
    with pytest.raises(ValueError):
        validate_ohlcv(row)


def test_identical_duplicate_dedup_is_not_conflicting_duplicate_acceptance():
    good = bar()
    assert unique_bars([good,good]) == [good]
    bad = dict(good,c=100.5)
    with pytest.raises(ValueError,match="conflicting"):
        unique_bars([good,bad])


def test_directional_markout_uses_future_completed_close_and_censors_missing_tail():
    bars=[dict(date=f"2026-07-{2+i:02}",close=100+i) for i in range(6)]
    long = directional_markouts(bars,0,"LONG")
    short = directional_markouts(bars,0,"SHORT")
    assert long["1"]["signed_close_change_pct"] == pytest.approx(1)
    assert short["5"]["signed_close_change_pct"] == pytest.approx(-5)
    assert long["10"] is None and long["trade_profit_equivalent"] is False
    result=summarize_markouts([dict(directional_markouts=long),dict(directional_markouts=short)])
    assert result["horizons_sessions"]["1"]["directional_positive_pct"] == 50
    assert result["horizons_sessions"]["10"]["directional_positive_pct"] is None


def test_unknown_pattern_direction_is_not_assumed_long():
    value=directional_markouts([dict(date="2026-07-02",close=100)],0,None)
    assert value["direction_known"] is False
    assert summarize_markouts([dict(directional_markouts=value)])["horizons_sessions"]["1"]["observations"] == 0
