"""Independent counterexamples and controls for the public-stock spot replay."""
from datetime import datetime, timezone
from contextlib import contextmanager
import json
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from scripts import scanner_history_stock as stock


def raw_bar(day="2026-07-02", **changes):
    opened = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York"))
    return {"t": opened.timestamp() * 1000, "o": 100., "h": 102., "l": 99.,
            "c": 101., "v": 1_000_000., **changes}


def write_source(directory, rows, *, multiplier=1, span="day"):
    source = {"symbol": "AAPL", "venue": "us_equity_polygon", "adjusted": True,
              "multiplier": multiplier, "span": span, "bars": rows,
              "bars_sha256": stock.fingerprint(rows)}
    (directory / f"stock-AAPL-{multiplier}{span}.json").write_text(json.dumps(source), encoding="utf-8")
    return source


def test_stock_replay_source_known_good_hash_and_schema(tmp_path):
    source = write_source(tmp_path, [raw_bar()])
    assert stock.load_source(tmp_path, "AAPL", 1, "day") == source


@pytest.mark.parametrize("rows", [
    [raw_bar(c=500)], [raw_bar(v=None)], [raw_bar(v=False)], [raw_bar(t=12345)],
    [raw_bar("2026-07-06"), raw_bar("2026-07-02")],
    [raw_bar(), raw_bar(c=100.5)], [raw_bar("2026-07-03")],
])
def test_stock_replay_closed_bad_history_cannot_be_hidden_by_normalizer(tmp_path, rows):
    # A checksum authenticates bytes, not correctness. Even with a matching
    # checksum the source must not let the adapter discard a closed observation.
    write_source(tmp_path, rows)
    with pytest.raises(ValueError):
        stock.load_source(tmp_path, "AAPL", 1, "day")


def test_stock_replay_intraday_off_grid_is_not_a_real_30minute_observation(tmp_path):
    row = raw_bar()
    row["t"] += 1  # A 1ms shift cannot become a half-hour bucket by truncation.
    write_source(tmp_path, [row], multiplier=30, span="minute")
    with pytest.raises(ValueError):
        stock.load_source(tmp_path, "AAPL", 30, "minute")


def decided(*, r=1., ambiguous=False, upper=2.):
    return {"entry_filled": True, "evaluation_status": "DECIDED", "outcome": "TP2",
            "r_multiple": r, "r_multiple_upper": upper, "intrabar_ambiguous": ambiguous}


def test_stock_replay_denominator_keeps_ambiguous_and_open_paths_out_of_unambiguous_rate():
    records = [decided(), decided(r=-1., upper=-.5), decided(r=-1., ambiguous=True, upper=2.),
               {"entry_filled": False, "outcome": "NO_FILL"},
               {"entry_filled": True, "outcome": "UNRESOLVED", "evaluation_status": "INCOMPLETE_HOLDING_WINDOW"}]
    result = stock.summarize_observations(records)
    assert result["unambiguous_decided"] == 2 and result["sample_win_rate_pct"] == 50
    assert result["decided_including_ambiguous_bounds"] == 3
    assert result["ambiguous"] == 1 and result["unresolved"] == result["no_fill"] == 1
    assert result["wilson95_pct"][0] < 50 < result["wilson95_pct"][1]


@pytest.mark.parametrize("value", [True, False])
def test_stock_replay_boolean_r_is_unknown_not_a_win_or_decided_path(value):
    result = stock.summarize_observations([decided(r=value)])
    assert result["unambiguous_decided"] == 0 and result["sample_win_rate_pct"] is None


def test_stock_replay_no_decided_paths_gives_unknown_rate_not_zero():
    result = stock.summarize_observations([])
    assert result["sample_win_rate_pct"] is None and result["wilson95_pct"] is None


def test_stock_research_fill_is_next_session_and_costs_not_same_candle_profit():
    bars = [dict(date="2026-07-02", open=100., high=130., low=50., close=100.),
            dict(date="2026-07-06", open=100., high=110., low=99., close=108.)]
    row = {"Ticker": "AAPL", "Entry": 100., "StopLoss": 98., "TP1": 104., "TP2": 108.}
    result = stock.model_candidate(bars, 0, row, "LONG")
    assert result["entry_date"] == "2026-07-06" and result["entry_filled"]
    assert result["roundtrip_fee_pct"] == .2 and result["exit_slippage_fraction"] == .001
    assert result["native_plan"]["entry_method"] == "market_at_signal"
    assert result["native_plan"]["plan_version"] == "stock_native_plan_next_open_spot_v1"
    assert result["live_delivery_equivalent"] is False


def write_frozen_cohort(directory, *, missing_day=None, bad_window=False):
    from scripts.scanner_history_bi import expected_sessions, WINDOW_START, WINDOW_END
    from datetime import date
    days = expected_sessions(date(2025, 10, 1), WINDOW_END)
    for symbol in stock.STOCKS:
        rows = [raw_bar(day) for day in days if not (symbol == "AAPL" and day == missing_day)]
        source = {"symbol": symbol, "venue": "us_equity_polygon", "adjusted": True,
                  "multiplier": 1, "span": "day", "bars": rows,
                  "bars_sha256": stock.fingerprint(rows)}
        (directory / f"stock-{symbol}-1day.json").write_text(json.dumps(source), encoding="utf-8")
        source = {**source, "multiplier": 30, "span": "minute"}
        (directory / f"stock-{symbol}-30minute.json").write_text(json.dumps(source), encoding="utf-8")
    protocol = {"stock_symbols": list(stock.STOCKS), "window": {
        "start_inclusive": "2026-07-03T00:00:00Z" if bad_window else "2026-07-02T00:00:00Z",
        "end_exclusive": "2026-10-02T00:00:00Z"}}
    (directory / "manifest.json").write_text(json.dumps(protocol), encoding="utf-8")


@contextmanager
def no_strategy_application(directory):
    # Run the real cohort/session preparation without importing another app,
    # scanning, provider calls or mail. Only its strategy list is empty.
    api = SimpleNamespace(PUBLIC_STOCK_STRATEGIES={}, datetime=datetime,
                          time=SimpleNamespace(time=lambda: 0))
    for name in ("_fetch_strategy_snapshot_universe", "fetch_stock_daily_history_strict",
                 "_fetch_strategy_daily_history", "_fetch_recent_stock_4h_bars",
                 "_load_common_stock_universe", "fetch_business_quality",
                 "_get_market_context_snapshot", "_strategy_cache_path"):
        setattr(api, name, lambda *a, **kw: None)
    yield api


def test_stock_replay_missing_one_asset_session_cannot_shrink_every_assets_cohort(tmp_path, monkeypatch):
    write_frozen_cohort(tmp_path, missing_day="2026-07-06")
    monkeypatch.setattr(stock, "isolated_application", no_strategy_application)
    monkeypatch.setattr(stock, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="session|coverage"):
        stock.replay(tmp_path, tmp_path / "output" / "result.json")


def test_stock_replay_checks_frozen_window_not_just_cohort_symbols(tmp_path, monkeypatch):
    write_frozen_cohort(tmp_path, bad_window=True)
    monkeypatch.setattr(stock, "isolated_application", no_strategy_application)
    monkeypatch.setattr(stock, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="window|manifest|frozen_cohort_mismatch"):
        stock.replay(tmp_path, tmp_path / "output" / "result.json")
