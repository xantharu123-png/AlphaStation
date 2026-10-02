"""Independent counterexamples for point-in-time historical cohorts.

No provider, live scheduler, account or SMTP access is involved.  In particular,
the universe counterexample changes only volume observed after the study start.
"""

from datetime import datetime, timedelta

import pytest

from modules import backtests


class _OctoberClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 2, 12, tzinfo=tz)


@pytest.mark.parametrize("engine", ["bi", "biotech"])
def test_initial_universe_cannot_depend_on_volume_after_study_start(monkeypatch, engine):
    from modules import signal_tracker

    monkeypatch.setattr(backtests, "datetime", _OctoberClock)
    monkeypatch.setattr(signal_tracker, "_is_us_equity_session", lambda day: day.weekday() < 5)
    monkeypatch.setattr(backtests, "BIOTECH_BACKTEST_UNIVERSE", ["OLD", "FUTR"])
    monkeypatch.setattr(
        backtests, "analyze_breakout_imminent",
        lambda *_args, **_kwargs: (False, 0, 20, [], 0, "D", 0, 0),
    )
    monkeypatch.setattr(
        backtests, "_compute_biotech_technical_from_bars",
        lambda *_args, **_kwargs: {"technical_score": 0, "rvol": 0},
    )

    def selected_with_future_volume(future_volume):
        selected = []

        def grouped(_key, day):
            rows = {"OLD": {"o": 20, "h": 21, "l": 19, "c": 20, "v": 1_000_000}}
            # FUTR has fewer than 50 completed observations at study inception.
            # Its initial 50-bar slice therefore consumes later study outcomes.
            if day >= "2026-06-01":
                volume = 600_000 if day < "2026-07-03" else future_volume
                rows["FUTR"] = {"o": 30, "h": 31, "l": 29, "c": 30, "v": volume}
            return rows

        def progress(_pct, message):
            if "Analysiere " in message:
                selected.append(message.split("Analysiere ", 1)[1].split()[0])

        monkeypatch.setattr(backtests, "fetch_grouped_daily", grouped)
        run = backtests.run_bi_v2_backtest if engine == "bi" else backtests.run_biotech_backtest
        run("offline-key", months=3, max_tickers=1, progress_callback=progress)
        return selected

    low_future_volume = selected_with_future_volume(600_000)
    high_future_volume = selected_with_future_volume(9_000_000)
    assert low_future_volume == ["OLD"]
    assert high_future_volume == low_future_volume, (
        "The initial ticker cap/ranking must use only observations available "
        "before study start, not a new listing's future 50-bar cohort."
    )


@pytest.mark.parametrize("engine", ["bi", "biotech"])
def test_initial_universe_does_not_require_future_survival_to_complete_a_full_history(monkeypatch, engine):
    from modules import signal_tracker

    monkeypatch.setattr(backtests, "datetime", _OctoberClock)
    monkeypatch.setattr(signal_tracker, "_is_us_equity_session", lambda day: day.weekday() < 5)
    monkeypatch.setattr(backtests, "BIOTECH_BACKTEST_UNIVERSE", ["OLD", "FUTR"])
    monkeypatch.setattr(backtests, "analyze_breakout_imminent",
                        lambda *_a, **_kw: (False, 0, 20, [], 0, "D", 0, 0))
    monkeypatch.setattr(backtests, "_compute_biotech_technical_from_bars",
                        lambda *_a, **_kw: {"technical_score": 0, "rvol": 0})

    def selected_with_last_date(last_future_date):
        selected = []

        def grouped(_key, day):
            rows = {"OLD": {"o": 20, "h": 21, "l": 19, "c": 20, "v": 1_000_000}}
            if "2026-06-01" <= day <= last_future_date:
                rows["FUTR"] = {"o": 30, "h": 31, "l": 29, "c": 30, "v": 2_000_000}
            return rows

        def progress(_pct, message):
            if "Analysiere " in message:
                selected.append(message.split("Analysiere ", 1)[1].split()[0])

        monkeypatch.setattr(backtests, "fetch_grouped_daily", grouped)
        run = backtests.run_bi_v2_backtest if engine == "bi" else backtests.run_biotech_backtest
        run("offline-key", months=3, max_tickers=1, progress_callback=progress)
        return selected

    truncated_future = selected_with_last_date("2026-07-31")
    complete_future = selected_with_last_date("2026-10-01")
    assert truncated_future == complete_future == ["FUTR"], (
        "The point-in-time universe must not preselect only instruments that "
        "will survive long enough to yield a future simulation window."
    )


@pytest.mark.parametrize("evaluation_status", ["INVALID_OHLC", "INVALID_VOLUME"])
def test_invalid_market_observations_are_partial_data_not_clean_coverage(evaluation_status):
    quality = backtests._backtest_data_quality([
        {"outcome": "UNRESOLVED", "evaluation_status": evaluation_status,
         "pnl_pct": None, "r_multiple": None}
    ])
    assert quality["status"] == "PARTIAL"
    assert quality["coverage_unresolved_trades"] == 1


def test_forward_calendar_gap_is_censored_not_a_fictional_stop_fill():
    bars = [
        {"date": "2026-09-21", "open": 100, "high": 103, "low": 99, "close": 101},
        {"date": "2026-09-23", "open": 80, "high": 82, "low": 79, "close": 81},
    ]
    result = backtests.simulate_50_50_daily_exit(
        bars, 0, 2, "LONG", 100, 95, 110, 120, fee_pct=0,
    )
    assert result["outcome"] == "UNRESOLVED"
    assert result["evaluation_status"] == "MISSING_EXPECTED_SESSION"
    assert result["missing_expected_sessions"] == ["2026-09-22"]
    assert result["pnl_pct"] is None


@pytest.mark.parametrize("direction,opening,expected", [
    ("LONG", 80, -20), ("SHORT", 120, -20),
])
def test_overnight_tail_loss_is_not_capped_to_the_nominal_stop(direction, opening, expected):
    bars = [{"date": "2026-09-22", "open": opening, "high": opening + 1,
             "low": opening - 1, "close": opening}]
    stop, tp1, tp2 = (95, 110, 120) if direction == "LONG" else (105, 90, 80)
    result = backtests.simulate_50_50_daily_exit(
        bars, 0, 1, direction, 100, stop, tp1, tp2, fee_pct=0,
    )
    assert result["pnl_pct"] == expected
    assert result["r_multiple"] == -4
    assert result["exit_reason"] == "STOP"


def test_costs_convert_notional_percent_to_initial_risk_units():
    bars = [{"date": "2026-09-22", "open": 100, "high": 103,
             "low": 97, "close": 100}]
    result = backtests.simulate_50_50_daily_exit(
        bars, 0, 1, "LONG", 100, 95, 110, 120, fee_pct=.2,
    )
    assert result["pnl_pct"] == -.2
    assert result["r_multiple"] == -.04


def test_no_loss_profit_factor_is_unbounded_not_a_magic_numeric_cap():
    trades = [{"is_winner": True, "pnl_pct": 10, "r_multiple": 2,
               "bars_held": 1, "outcome": "TP2", "exit_reason": "TP2"}]
    result = backtests.compute_backtest_stats(trades)
    assert result["profit_factor"] is None
    assert result["profit_factor_unbounded"] is True
    assert result["profit_factor_display"] == "INF"


def test_profit_factor_comparison_uses_raw_ratio_not_display_rounding():
    from modules.performance_metrics import profit_factor_metrics

    metric = profit_factor_metrics(1.099, 1.0)
    assert metric["value"] == 1.1
    assert metric["display"] == "1.10"
    assert metric["comparison_value"] == pytest.approx(1.099)
    assert metric["comparison_value"] < 1.1


@pytest.mark.parametrize("unavailable_future_pnl", [100.0, -100.0])
def test_holdout_training_cannot_consume_outcomes_from_the_holdout_future(unavailable_future_pnl):
    import api

    start = datetime(2026, 8, 3)
    trades = []
    for index in range(40):
        day = (start + timedelta(days=index)).date().isoformat()
        trades.append({"ticker": f"QA{index:02d}", "entry_date": day,
                       "signal_date": day, "exit_date": day,
                       "pnl_pct": 1.0, "r_multiple": .2, "outcome": "TP2"})
    split_date = (start + timedelta(days=32)).date().isoformat()
    trades[0].update(exit_date="2026-09-30", pnl_pct=unavailable_future_pnl)
    trades[1]["exit_date"] = split_date

    result = api._bt_out_of_sample_summary(trades)
    assert result["split_date"] == split_date
    assert result["in_sample"]["total_trades"] == 30, (
        "Training rows whose economic outcome was not known before holdout "
        "inception must be purged, even when their entry predates the split."
    )
    assert result["in_sample"]["avg_pnl"] == 1.0
    assert result["holdout"]["total_trades"] == 8
    assert result["holdout"]["avg_pnl"] == 1.0
    assert result["purged_in_sample_trades"] == 2


def _crypto_pattern_bars(start, *, short=False):
    bars = []
    for index in range(50):
        price = 100.0 if short else 100 + index * .4
        bars.append({"date": (start + timedelta(days=index)).date().isoformat(),
                     "open": price, "high": price + 1, "low": price - 1,
                     "close": price, "volume": 1_000_000, "is_closed": True})
    if short:
        for index, price in enumerate([130, 140, 155, 165, 180], start=24):
            bars[index].update(open=price - 2, high=price + 1, low=price - 3, close=price)
        bars[29].update(open=188, high=196, low=185, close=190)
        bars[30].update(open=185, high=187, low=158, close=160, volume=1_600_000)
        bars[31].update(open=160, high=161, low=156, close=158)
        bars[32].update(open=158, high=159, low=152, close=155)
    else:
        bars[30].update(open=112, high=116, low=111, close=115, volume=1_600_000)
        bars[31].update(open=115, high=117, low=114, close=116)
        bars[32].update(open=116.01, high=118, low=115, close=117)
    return bars


def _offline_crypto_feed(monkeypatch, api, bars):
    monkeypatch.setattr(api, "datetime", _OctoberClock)
    monkeypatch.setattr(api, "_crypto_backtest_universe",
                        lambda _max: [{"id": "synthetic-qa", "symbol": "qa"}])
    # The old engine used the unvalidated reader; the audited one adds the
    # validation adapter. Both reproduce the same bounded synthetic daily feed.
    helper = ("_validated_exchange_daily_crypto_bars" if hasattr(api, "_validated_exchange_daily_crypto_bars")
              else "_exchange_daily_crypto_bars")
    monkeypatch.setattr(api, helper, lambda *_args, **_kwargs: (bars, "offline-qa"))
    monkeypatch.setattr(api, "_bt_atr", lambda *_args, **_kwargs: 2.0)


@pytest.mark.parametrize("short", [False, True])
def test_crypto_pattern_entry_is_after_confirmation_not_at_its_open(monkeypatch, short):
    import api

    bars = _crypto_pattern_bars(datetime(2026, 8, 1), short=short)
    _offline_crypto_feed(monkeypatch, api, bars)
    observed = []
    original_simulate = api._simulate_crypto_trade

    def execution(bars, index, direction, entry, stop, tp1, tp2, max_hold):
        observed.append((index, direction, entry, max_hold))
        return original_simulate(bars, index, direction, entry, stop, tp1, tp2, max_hold)

    monkeypatch.setattr(api, "_simulate_crypto_trade", execution)
    strategy = "crypto_pump_dump_short" if short else "crypto_early_mover_long"
    result = api._run_crypto_backtest(api.BacktestRequest(
        ticker="UNIVERSE", strategy=strategy, months=3, max_tickers=5,
        job_id="independent_crypto_causality",
    ))
    assert observed == [(32, "short" if short else "long", bars[32]["open"], 6 if short else 8)]
    assert result["total_trades"] == 1
    assert result["trades"][0]["entry_date"] == bars[32]["date"]


def test_crypto_warmup_observations_are_not_signals_in_the_requested_study_period(monkeypatch):
    import api

    bars = _crypto_pattern_bars(datetime(2026, 6, 1))
    _offline_crypto_feed(monkeypatch, api, bars)
    calls = []

    def forbidden_early_execution(*args, **kwargs):
        calls.append(args[0][args[1]]["date"])
        return None

    monkeypatch.setattr(api, "_simulate_crypto_trade", forbidden_early_execution)
    result = api._run_crypto_backtest(api.BacktestRequest(
        ticker="UNIVERSE", strategy="crypto_early_mover_long", months=3,
        max_tickers=5, job_id="independent_crypto_warmup",
    ))
    assert calls == [], "A July-01 signal is outside the July-04 study start; it is warmup only."
    assert result["total_trades"] == 0


def test_crypto_24_hour_calendar_does_not_silently_ignore_missing_weekend_prices():
    import api

    bars = [
        {"date": "2026-09-25", "open": 100, "high": 101, "low": 99, "close": 100},
        {"date": "2026-09-28", "open": 100, "high": 101, "low": 99, "close": 100},
    ]
    result = api._simulate_crypto_trade(bars, 0, "long", 100, 95, 110, 120, 2, fee_pct=0)
    assert result["outcome"] == "UNRESOLVED"
    assert result["missing_expected_sessions"] == ["2026-09-26", "2026-09-27"]
    assert result["pnl_pct"] is None


def test_twelve_month_crypto_study_keeps_its_declared_indicator_warmup(monkeypatch):
    import api

    _offline_crypto_feed(monkeypatch, api, [])
    observed_days = []
    helper = ("_validated_exchange_daily_crypto_bars" if hasattr(api, "_validated_exchange_daily_crypto_bars")
              else "_exchange_daily_crypto_bars")

    def empty_feed(_coin, days):
        observed_days.append(days)
        return [], None

    monkeypatch.setattr(api, helper, empty_feed)
    api._run_crypto_backtest(api.BacktestRequest(
        ticker="UNIVERSE", strategy="crypto_early_mover_long", months=12,
        max_tickers=5, job_id="independent_crypto_year_warmup",
    ))
    assert observed_days == [360 + 45], (
        "The declared 12-month signal cohort still needs its 45 prior daily "
        "observations; a legacy 365-bar cap must not remove them."
    )
