"""Gap contract audit with synthetic bars and no provider/SMTP access."""
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules import stock_swing_contract as swing
from test_stock_momentum_confirmed_contract import _metrics, _wrapper_fixture


SESSION = "2026-09-29"
CUTOFF = datetime(2026, 9, 29, 20, tzinfo=timezone.utc)


def _daily_history(volume=300_000, direction="Long"):
    dates = []
    day = datetime(2026, 9, 28).date()
    while len(dates) < 70:
        if swing.session_close(day.isoformat()) is not None:
            dates.append(day.isoformat())
        day -= timedelta(days=1)
    history = [{"date": date, "open": 99.0, "high": 101.0,
                "low": 98.0, "close": 100.0, "volume": 100_000}
               for date in reversed(dates)]
    open_, high, low, close = (103.5, 106., 103., 105.)
    if direction == "Short":
        open_, high, low, close = (96.5, 97., 94., 95.)
    return history + [{"date": SESSION, "open": open_, "high": high,
                       "low": low, "close": close, "volume": volume}]


def _gap_fixture(monkeypatch, direction="Long", *, metrics=None, volume=300_000):
    _wrapper_fixture(monkeypatch)
    now = datetime(2026, 9, 30, 0, tzinfo=timezone.utc)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    price, open_, high, low = (105., 103.5, 106., 103.)
    if direction == "Short":
        price, open_, high, low = (95., 96.5, 97., 94.)
    observation = {"ticker": "TEST", "day": {
        "o": open_, "h": high, "l": low, "c": price, "v": volume,
    }, "prevDay": {"c": 100., "v": 100_000}, **swing.metadata(SESSION, price)}
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: [observation])
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: _daily_history(volume, direction))
    monkeypatch.setattr(api, "_stock_previous_session_change", lambda *a, **kw: 0.)
    # The strategy scorer has already assessed its own candle/volume/risk
    # evidence. This isolated test examines accidental later cross-strategy caps.
    monkeypatch.setattr(api, "_score_strategy_candidate", lambda **kw: (90, {
        "direction": direction.lower(), "atr_pct": 3., "extension_atr": 1.5,
        "upper_wick_pct": 10., "lower_wick_pct": 10., "setup_score": 90,
    }))
    monkeypatch.setattr(api, "_enrich_stock_strategy_native_plan", lambda *a: None)
    if metrics is not None:
        monkeypatch.setattr(api, "_strategy_daily_history_metrics", lambda *a, **kw: metrics)
    return f"Gap Momentum {direction}"


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("range_position", [70., 85.])
def test_gap_score_is_not_capped_by_unrelated_long_high_breakout_rules(
    monkeypatch, direction, range_position,
):
    metrics = _metrics(high_10d=120., high_20d=125., breakout_10d_pct=-12.,
                       breakout_20d_pct=-16., range_pos=range_position,
                       ema20=85., ema50=84.)
    name = _gap_fixture(monkeypatch, direction, metrics=metrics)
    rows = api._strategy_scan_wrapper(name, send_email=False)
    assert len(rows) == 1
    assert rows[0]["Gap_Pct"] == (3.5 if direction == "Long" else -3.5)
    assert rows[0]["Change_Pct"] == (5.0 if direction == "Long" else -5.0)
    assert rows[0]["base_score"] == rows[0]["score"] == 90
    assert rows[0]["Momentum_Breakout_Type"] == (
        "GAP_UP_MOMENTUM" if direction == "Long" else "GAP_DOWN_MOMENTUM"
    )


def test_gap_daily_metrics_exclude_signal_volume_and_future_sessions():
    history = _daily_history()
    future = {**history[-1], "date": "2026-09-30", "high": 999.,
              "close": 998., "volume": 99_000_000}
    inputs = dict(price=105., day_open=103.5, day_high=106., day_low=103.,
                  day_volume=300_000, now_utc=CUTOFF, signal_session=SESSION,
                  include_structure=False)
    baseline = api._strategy_daily_history_metrics(history, **inputs)
    with_future = api._strategy_daily_history_metrics([future, *reversed(history)], **inputs)
    assert baseline == with_future
    assert baseline["avg_vol20"] == 100_000
    assert baseline["rvol20_raw"] == baseline["rvol20"] == 3.
    assert baseline["high_20d"] == 101.
    assert baseline["expected_volume_fraction"] == 1.


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_completed_daily_gap_ignores_unrelated_extended_trade_price(monkeypatch, direction):
    name = _gap_fixture(monkeypatch, direction, metrics=_metrics())
    observation = api._fetch_strategy_snapshot_universe(name)[0]
    observation["lastTrade"] = {"p": 500., "t": 1_790_760_000_000_000_000}
    monkeypatch.setattr(api, "get_current_trading_session", lambda: ("After-Hours", "After-Hours"))
    rows = api._strategy_scan_wrapper(name, send_email=False)
    assert len(rows) == 1
    row = rows[0]
    assert row["price"] == (105. if direction == "Long" else 95.)
    assert row["Gap_Pct"] == (3.5 if direction == "Long" else -3.5)
    assert row["Change_Pct"] == (5. if direction == "Long" else -5.)
    assert row["Extended_Hours"] is False
    assert row["scan_price_source"] == swing.SOURCE
    assert row["scan_price_observed_at"] == "2026-09-29T20:00:00Z"


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("volume,allowed", [(149_600, False), (150_000, True),
                                           (150_400, True)])
def test_gap_rvol_floor_uses_unrounded_evidence(monkeypatch, direction, volume, allowed):
    name = _gap_fixture(monkeypatch, direction, volume=volume)
    # Undo the injected metrics function from the reusable generic fixture.
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", REAL_METRICS)
    rows = api._strategy_scan_wrapper(name, send_email=False)
    # 149600 / 100000 = 1.496, below the contractual 1.5 floor.
    assert bool(rows) is allowed


def test_senior_notes_are_rejected_despite_provider_common_stock_label():
    assert api._reference_asset_exclusion_reason(
        "CS", "BRC Group Holdings, Inc. 6.00% Senior Notes Due 2028", "stocks",
    ) is not None


def test_stale_common_stock_cache_cannot_override_explicit_senior_note_name(monkeypatch):
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "names", {
        "RILYT": "BRC Group Holdings, Inc. 6.00% Senior Notes Due 2028",
    })
    assert api._stock_alert_asset_exclusion_reason(
        "RILYT", common_stock_universe={"RILYT"}, universe_source="cached_fixture",
    ) is not None


@pytest.mark.parametrize("name", ["Bond Street Holdings, Inc.", "Notes Live, Inc.",
                                 "Senior Living Holdings Common Stock"])
def test_equity_company_names_are_not_rejected_by_broad_debt_words(name):
    assert api._reference_asset_exclusion_reason("CS", name, "stocks") is None


REAL_METRICS = api._strategy_daily_history_metrics
