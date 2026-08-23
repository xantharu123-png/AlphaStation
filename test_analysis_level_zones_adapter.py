from datetime import datetime, timedelta, timezone

from modules.analysis import calculate_sr_from_historical


UTC = timezone.utc
BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _tuple_bar(opened_at, high, low, close, open_=None, volume=1_000):
    return [
        int(opened_at.timestamp() * 1_000),
        close if open_ is None else open_,
        high,
        low,
        close,
        volume,
    ]


def _daily_bars():
    values = [
        (102, 98, 100),
        (104, 99, 103),
        (106, 100, 105),
        (103, 98, 100),
        (101, 95, 97),
        (103, 96, 101),
        (105, 99, 104),
    ]
    return [
        _tuple_bar(BASE + timedelta(days=index), high, low, close)
        for index, (high, low, close) in enumerate(values)
    ]


def test_daily_adapter_is_causal_and_keeps_legacy_shape_with_zone_metadata():
    completed = _daily_bars()
    cutoff = BASE + timedelta(days=7)
    running_and_future = [
        _tuple_bar(BASE + timedelta(days=7), 999, 1, 500),
        _tuple_bar(BASE + timedelta(days=8), 1_999, 0.5, 900),
    ]

    prefix = calculate_sr_from_historical(
        completed, 102.0, timeframe="1D", as_of=cutoff
    )
    with_future = calculate_sr_from_historical(
        completed + running_and_future,
        102.0,
        timeframe="1D",
        as_of=cutoff,
    )

    (supports, resistances), info = prefix
    (future_supports, future_resistances), future_info = with_future
    assert supports == future_supports
    assert resistances == future_resistances
    assert info["zones"] == future_info["zones"]
    assert info["period_high"] == future_info["period_high"] == 106.0
    assert info["period_low"] == future_info["period_low"] == 95.0
    assert info["total_candles"] == 7
    assert future_info["zone_provenance"]["excluded_uncompleted_or_invalid_bars"] == 2

    # Latest verifiably completed daily session, not a positional raw candle.
    assert info["prev_day_high"] == 105.0
    assert info["prev_day_low"] == 99.0
    assert info["prev_day_close"] == 104.0
    assert info["session_levels"] == {"PDC": 104.0, "PDH": 105.0, "PDL": 99.0}
    assert info["session_level_reason"] == "latest_verifiably_completed_daily_session"

    assert isinstance(supports, list)
    assert isinstance(resistances, list)
    assert info["supports_detail"] or info["resistances_detail"]
    for row in info["supports_detail"] + info["resistances_detail"]:
        assert {"price", "type", "strength"}.issubset(row)
        assert row["zone_low"] <= row["price"] <= row["zone_high"]
        assert row["zone_high"] > row["zone_low"]
        assert row["provenance"]["adaptive_zone"] is True

    provenance = info["zone_provenance"]
    assert provenance["model"] == "causal_level_zones_v1"
    assert provenance["causal_completed_bars"] is True
    assert provenance["adaptive_zone_width"] is True
    assert provenance["fixed_percent_cluster_used"] is False
    assert provenance["standalone_fibonacci_strength_used"] is False
    assert info["fibonacci_provenance"]["used_as_structural_evidence"] is False
    assert all(
        evidence["source_family"] != "fibonacci"
        for zone in info["zones"]
        for evidence in zone["evidence"]
    )


def test_intraday_tuple_never_labels_penultimate_candle_as_previous_day():
    bars = []
    for index in range(12):
        center = 100 + (index % 4)
        bars.append(_tuple_bar(
            BASE + timedelta(minutes=15 * index),
            center + 2,
            center - 2,
            center,
        ))
    bars.append(_tuple_bar(BASE + timedelta(minutes=15 * 12), 999, 1, 500))
    cutoff = BASE + timedelta(minutes=15 * 12)

    (_supports, _resistances), info = calculate_sr_from_historical(
        bars,
        101.0,
        timeframe="15Min",
        as_of=cutoff,
        direction="SHORT",
    )

    assert info["total_candles"] == 12
    assert info["period_high"] < 999
    assert info["prev_day_high"] is None
    assert info["prev_day_low"] is None
    assert info["prev_day_close"] is None
    assert info["session_levels"] == {}
    assert info["session_levels_available"] is False
    assert info["session_level_reason"] == (
        "intraday_source_has_no_verified_trading_session_calendar"
    )
    assert info["zone_provenance"]["timeframe"] == "15M"
    assert info["zone_provenance"]["direction"] == "SHORT"
    assert not any(
        evidence["source_name"] in {"PDH", "PDL", "PDC"}
        for zone in info["zones"]
        for evidence in zone["evidence"]
    )


def test_weekly_session_uses_weekly_labels_without_populating_previous_day_fields():
    bars = [
        _tuple_bar(BASE + timedelta(weeks=index), 110 + index, 90 + index, 100 + index)
        for index in range(6)
    ]
    cutoff = BASE + timedelta(weeks=6)

    (_supports, _resistances), info = calculate_sr_from_historical(
        bars, 104.0, timeframe="1W", as_of=cutoff
    )

    assert set(info["session_levels"]) == {"PWC", "PWH", "PWL"}
    assert info["prev_day_high"] is None
    assert info["prev_day_low"] is None
    assert info["prev_day_close"] is None
    assert info["session_level_reason"] == (
        "weekly_session_labels_available_as_PWH_PWL_PWC"
    )


def test_unverifiable_history_fails_closed_without_fixed_percent_levels():
    untimestamped = [
        {"open": 100, "high": 102 + index, "low": 98, "close": 101, "volume": 1_000}
        for index in range(6)
    ]

    (supports, resistances), info = calculate_sr_from_historical(
        untimestamped,
        101.0,
        timeframe="1D",
        as_of=BASE + timedelta(days=10),
    )

    assert supports == []
    assert resistances == []
    assert info["available"] is False
    assert info["availability_reason"] == "insufficient_completed_timestamped_bars"
    assert info["zone_provenance"]["fixed_percent_cluster_used"] is False
