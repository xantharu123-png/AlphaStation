"""Sector replay must not invent event/float/quote inputs or full signal rates."""
from datetime import date, timedelta

import pytest

from scripts import scanner_history_sector as sector


def bars():
    rows, day = [], date(2026, 1, 2)
    while len(rows) < 100:
        if sector.session_close(day.isoformat()) is not None:
            rows.append({"date": day.isoformat(), "open": 20., "high": 21., "low": 19.,
                         "close": 20., "volume": 1_000_000.})
        day += timedelta(days=1)
    return rows


def window(rows):
    return {"window_start": date.fromisoformat(rows[95]["date"]),
            "window_end": date.fromisoformat(rows[97]["date"]) + timedelta(days=1)}


def test_sector_biotech_runs_real_completed_technical_adapter_not_event_score():
    rows = bars()
    report = sector.replay_biotech_technical(rows, "AMGN", **window(rows))
    assert report["analysis_sessions"] == 3 and report["full_scanner_candidates"] is None
    assert report["win_rate_pct"] is None and report["event_score_invented"] is False
    for record in report["records"]:
        assert 50 <= record["query_completed_bars"] <= 66
        assert record["details"]["analysis_session"] == record["session"]
        assert record["details"]["analysis_as_of"] == record["decision_as_of"]
        assert record["technical_model"] == "biotech_completed_bar_v3"


def test_sector_future_prices_cannot_change_earlier_biotech_scores():
    rows = bars()
    earlier = sector.replay_biotech_technical(rows, "AMGN", **window(rows))
    rows[-1].update(close=200., high=500., volume=9_000_000_000)
    later = sector.replay_biotech_technical(rows, "AMGN", **window(rows))
    assert earlier == later


def test_sector_penny_daily_levels_do_not_turn_missing_5m_quote_float_into_trade():
    rows = bars()
    report = sector.replay_penny_daily_levels(rows, "SIRI", **window(rows))
    assert report["analysis_sessions"] == 3
    assert report["price_only_range_sessions"] == 0
    assert report["sessions_with_overhead_daily_level"] == 3
    assert report["full_scanner_candidates"] is None and report["win_rate_pct"] is None
    assert report["no_trade_claim"] and len(report["missing_inputs"]) == 4


def test_sector_future_penny_high_cannot_become_previous_daily_resistance():
    rows = bars()
    earlier = sector.replay_penny_daily_levels(rows, "SIRI", **window(rows))
    rows[-1].update(high=500.)
    later = sector.replay_penny_daily_levels(rows, "SIRI", **window(rows))
    assert earlier == later


@pytest.mark.parametrize("function,symbol", [
    (sector.replay_biotech_technical, "AMGN"), (sector.replay_penny_daily_levels, "SIRI")])
def test_sector_missing_session_does_not_silently_shorten_history(function, symbol):
    rows = bars()
    study = window(rows)
    del rows[96]
    with pytest.raises(ValueError, match="missing_session"):
        function(rows, symbol, **study)
