from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import api

from scripts.scanner_history_special_stocks import (
    orb_technical_event, intraday_directional_markouts, run_daily_observation_wrappers)

UTC = timezone.utc


def raw_bar(timestamp, close=100.0, volume=1_000_000.0):
    return {"t": int(timestamp.timestamp()*1000), "o": close, "h": close+1,
            "l": close-1, "c": close, "v": volume}


def orb_fixture(short=False):
    opening = datetime(2026, 9, 29, 13, 30, tzinfo=UTC)
    bars = [raw_bar(opening+timedelta(minutes=5*i), volume=100_000) for i in range(4)]
    bars[3].update(o=100.0, h=101.7 if not short else 100.1, l=99.9 if not short else 98.3,
                   c=101.5 if not short else 98.5, v=250_000.0)
    previous = {"c": 97.0 if not short else 103.0, "v": 1_000_000.0, "h": 104.0, "l": 96.0}
    return bars, previous, opening+timedelta(minutes=35)


@pytest.mark.parametrize("short", [False, True])
def test_orb_technical_stage_uses_actual_completed_range_and_volume_helpers(short):
    bars, previous, available = orb_fixture(short)
    event, reason = orb_technical_event(api, bars, previous, daily_prefix=[], as_of=available, symbol="AAPL")
    assert reason is None
    assert event["direction"] == ("SHORT" if short else "LONG")
    assert event["active_excursion_volume"]["confirmed"] is True
    assert event["production_volume_points"] == 25
    assert event["native_trade_plan_checked"] is False
    assert event["final_mail_gate_checked"] is False


def test_orb_open_bar_and_future_day_prices_do_not_rewrite_causal_event():
    bars, previous, available = orb_fixture()
    expected = orb_technical_event(api, bars, previous, daily_prefix=[], as_of=available, symbol="AAPL")
    future = raw_bar(available, close=100000, volume=90000000)
    future.update(o=None, h=None, l=None, c=None)
    assert orb_technical_event(api, bars+[future], previous, daily_prefix=[], as_of=available, symbol="AAPL") == expected


def test_orb_missing_one_opening_bar_is_not_a_shortened_opening_range():
    bars, previous, available = orb_fixture()
    event, reason = orb_technical_event(api, [bars[0]]+bars[2:], previous, daily_prefix=[], as_of=available, symbol="AAPL")
    assert event is None and reason == "opening_range_incomplete"


def test_orb_atr_keeps_asset_identity_and_chronological_35_calendar_day_prefix(monkeypatch):
    bars, previous, available = orb_fixture()
    history = [raw_bar(datetime(2026, 9, d, 4, tzinfo=UTC)) for d in (1, 2, 3, 28, 29, 30)]
    calls = []
    def atr(symbol, watermark, fallback):
        calls.append((symbol, watermark, api.rate_limited_get("frozen").json()["results"]))
        return 2.0, "fixture_real_method_boundary"
    monkeypatch.setattr(api, "_fetch_orb_atr_pct", atr)
    orb_technical_event(api, bars, previous, daily_prefix=history, as_of=available, symbol="MSFT")
    assert calls[0][0] == "MSFT"
    assert calls[0][1] == available-timedelta(minutes=15)
    assert calls[0][2] == history[:4]


def test_orb_directional_diagnostic_uses_known_signal_price_and_censors_end():
    bars = [{"date": f"2026-09-{d:02}", "close": float(d+100)} for d in range(10, 14)]
    result = intraday_directional_markouts(bars, 0, "SHORT", 100.0)
    assert result["reference_close"] == 100
    assert result["1"]["signed_close_change_pct"] == pytest.approx(-11.0)
    assert result["5"] is None and result["10"] is None
    assert result["trade_profit_equivalent"] is False


def test_volume_spikes_uses_actual_raw_daily_prev_proxy_not_invented_twenty_day_average(monkeypatch):
    current = raw_bar(datetime(2026, 9, 29, 4, tzinfo=UTC), close=104, volume=3_100_000)
    previous = raw_bar(datetime(2026, 9, 28, 4, tzinfo=UTC), close=100, volume=1_000_000)
    monkeypatch.setattr(api, "_bear_scan_wrapper", lambda: None)
    spikes, bears = run_daily_observation_wrappers(api,
        [{"ticker": "AAPL", "day": current, "prevDay": previous}], {"AAPL": [previous, current]},
        as_of=datetime(2026, 9, 29, 20, 15, tzinfo=UTC))
    assert bears == []
    assert len(spikes) == 1
    assert spikes[0]["rvol"] == 3.1
    assert spikes[0]["rvol_source"] == "prev_day_completed_session_proxy"
    assert spikes[0]["signal_type"] == "BREAKOUT"
    assert spikes[0]["execution_trigger_ok"] is False


def bear_snapshot_fixture():
    from modules.stock_swing_contract import NY
    from scripts.scanner_history_bi import expected_sessions
    as_of = datetime(2026, 9, 29, 20, 15, tzinfo=UTC)
    days = expected_sessions(datetime(2026, 6, 1).date(), datetime(2026, 9, 30).date())[-70:]
    history = [raw_bar(datetime.fromisoformat(d).replace(tzinfo=NY)) for d in days]
    previous = history[-2]
    history[-1].update(o=100.0, h=101.0, l=94.0, c=96.0, v=2_000_000.0)
    return as_of, history, previous


def test_actual_bear_discovery_retains_observed_drop_without_claiming_mail(monkeypatch):
    as_of, history, previous = bear_snapshot_fixture()
    def no_mail(*a, **kw):
        pytest.fail("Historical technical discovery never authorizes a mail")
    monkeypatch.setattr(api, "_send_email_alert", no_mail)
    spikes, bears = run_daily_observation_wrappers(api,
        [{"ticker": "AAPL", "day": history[-1], "prevDay": previous}], {"AAPL": history}, as_of=as_of)
    assert spikes == []
    assert [b["ticker"] for b in bears] == ["AAPL"]
    assert bears[0]["change_pct"] == pytest.approx(-4.0)
    assert bears[0]["direction"] == "SHORT"
    assert bears[0]["score"] > 0


@pytest.mark.parametrize("field,value", [("v", float("inf")), ("h", 95.0), ("o", 0.0)])
def test_bear_discovery_rejects_invalid_signal_snapshot_without_losing_valid_peer(monkeypatch, field, value):
    as_of, history, previous = bear_snapshot_fixture()
    bad = {"ticker": "MSFT", "day": deepcopy(history[-1]), "prevDay": deepcopy(previous)}
    bad["day"][field] = value
    good = {"ticker": "AAPL", "day": history[-1], "prevDay": previous}
    spikes, bears = run_daily_observation_wrappers(api, [bad, good], {"AAPL": history, "MSFT": history}, as_of=as_of)
    assert [row["ticker"] for row in bears] == ["AAPL"]


@pytest.mark.parametrize("container,field,value", [
    ("prevDay", "v", True), ("prevDay", "c", 0.0), ("day", "v", float("inf")),
])
def test_volume_spikes_invalid_required_snapshot_measurements_do_not_create_patterns(monkeypatch, container, field, value):
    current = raw_bar(datetime(2026, 9, 29, 4, tzinfo=UTC), close=104, volume=3_100_000)
    previous = raw_bar(datetime(2026, 9, 28, 4, tzinfo=UTC), close=100, volume=1_000_000)
    bad = {"ticker": "MSFT", "day": deepcopy(current), "prevDay": deepcopy(previous)}
    bad[container][field] = value
    good = {"ticker": "AAPL", "day": current, "prevDay": previous}
    monkeypatch.setattr(api, "_bear_scan_wrapper", lambda: None)
    spikes, _ = run_daily_observation_wrappers(api, [bad, good], {"AAPL": [previous, current], "MSFT": [previous, current]},
        as_of=datetime(2026, 9, 29, 20, 15, tzinfo=UTC))
    assert [row["ticker"] for row in spikes] == ["AAPL"]
