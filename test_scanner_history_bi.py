"""Offline causal/coverage/outcome checks for the fixed-cohort BI replay."""
from datetime import date, datetime, timedelta, timezone
import json
from zoneinfo import ZoneInfo

import pytest

from scripts import scanner_history_bi as replay


class Analysis(tuple):
    def __new__(cls, *, valid=True, green=17, available=20, contract=True, gates=()):
        obj = super().__new__(cls, (valid, 100, 173, [], 75, "A", 3, 4))
        obj.green_count, obj.available_count = green, available
        obj.indicator_contract_ok, obj.hard_gate_failures = contract, gates
        obj.consolidation_days = 5
        return obj


def daily_bars(count=97):
    rows, day = [], date(2026, 1, 2)
    while len(rows) < count:
        if replay.session_close(day.isoformat()) is not None:
            rows.append({"date": day.isoformat(), "time": day.isoformat(), "open": 100.,
                         "high": 101., "low": 99., "close": 100., "volume": 1_000_000.})
        day += timedelta(days=1)
    return rows


def studied_window(bars, start=90, end=None):
    return {"window_start": date.fromisoformat(bars[start]["date"]),
            "window_end": date.fromisoformat(bars[end or -1]["date"]) + timedelta(days=1)}


def accepted_plan(*args, **kwargs):
    return {"accepted": True, "Entry": 100., "StopLoss": 98., "TP1": 104., "TP2": 108.,
            "entry_method": "market_at_signal", "plan_version": replay.BI_PLAN_VERSION}


def nofill(bars, index, plan, direction, **kwargs):
    return {"entry_filled": False, "outcome": "NO_FILL", "evaluation_status": "ENTRY_NOT_REACHED",
            "exit_date": bars[index]["date"] if index < len(bars) else None}


@pytest.mark.parametrize("result", [
    Analysis(valid=False), Analysis(green=16), Analysis(available=19),
    Analysis(contract=False), Analysis(gates=("negative_direction",)),
])
def test_history_bi_never_substitutes_weighted_score_for_contract(result):
    bars = daily_bars()
    report = replay.replay_asset(bars, "TEST", "long", **studied_window(bars),
        analyzer=lambda *a, **kw: result, planner=lambda *a, **kw: pytest.fail("invalid contract got a plan"))
    assert report["analysis_sessions"] == 7
    assert report["raw_selected"] == report["native_plan_valid"] == 0
    assert report["win_rate_lower_pct"] is None


def test_history_bi_uses_exact_closed_prefix_and_starter_availability_then_next_session():
    bars, decisions = daily_bars(), []
    def analyze(prefix, **kw):
        assert len(prefix) == 50
        decisions.append(prefix[-1]["date"])
        return Analysis()
    def plan(prefix, **kw):
        assert len(prefix) == 90 and prefix[-1]["date"] == decisions[-1]
        assert kw["as_of"] == replay.session_close(decisions[-1]) + timedelta(seconds=900)
        assert kw["live_price"] == prefix[-1]["close"]
        return accepted_plan()
    def execute(observed, start, plan, direction, **kw):
        assert observed[start - 1]["date"] == decisions[-1]
        assert kw["horizon_bars"] == 10
        if start == len(observed):
            return {"entry_filled": False, "outcome": "UNRESOLVED",
                    "evaluation_status": "INCOMPLETE_ENTRY_WINDOW"}
        assert observed[start]["date"] > decisions[-1]
        return nofill(observed, start, plan, direction)
    report = replay.replay_asset(bars, "TEST", "long", **studied_window(bars),
        analyzer=analyze, planner=plan, simulator=execute)
    assert report["raw_selected"] == 7 and report["native_plan_valid"] == 7
    assert report["simulated_nonoverlapping"] == 4
    assert report["overlap_excluded"] == 3


def test_history_bi_censors_future_outcome_bars_and_keeps_open_not_lost():
    bars = daily_bars(98)
    window = studied_window(bars, end=95)
    def execute(observed, start, *a, **kw):
        assert observed[-1]["date"] == bars[95]["date"]
        return {"entry_filled": True, "outcome": "UNRESOLVED",
                "evaluation_status": "INCOMPLETE_HOLDING_WINDOW", "pnl_pct": None,
                "exit_date": observed[-1]["date"]}
    report = replay.replay_asset(bars, "TEST", "short", **window,
        analyzer=lambda *a, **kw: Analysis(), planner=accepted_plan, simulator=execute)
    assert report["raw_selected"] == 6
    assert report["simulated_nonoverlapping"] == report["filled"] == report["open"] == 1
    assert report["decided"] == 0 and report["win_rate_lower_pct"] is None


def test_history_bi_count_native_plan_rejections_without_fake_trades():
    bars = daily_bars()
    report = replay.replay_asset(bars, "TEST", "long", **studied_window(bars),
        analyzer=lambda *a, **kw: Analysis(),
        planner=lambda *a, **kw: {"accepted": False, "reason": "structural_barrier_blocked"})
    assert report["raw_selected"] == 7 and report["native_plan_valid"] == 0
    assert report["native_plan_rejections"] == {"structural_barrier_blocked": 7}
    assert len(report["examples"]) == 3
    assert [row["signal_session"] for row in report["examples"]] == [row["date"] for row in bars[90:93]]


def test_history_bi_uses_later_ambiguous_exit_for_dedup_and_separate_rates():
    bars = daily_bars()
    def execute(observed, start, *a, **kw):
        return {"entry_filled": True, "outcome": "STOP", "evaluation_status": "DECIDED",
                "pnl_pct": -2.3, "pnl_pct_upper": 5.7, "intrabar_ambiguous": True,
                "exit_date": observed[start]["date"],
                "exit_date_upper": observed[min(start + 2, len(observed) - 1)]["date"]}
    report = replay.replay_asset(bars, "TEST", "long", **studied_window(bars),
        analyzer=lambda *a, **kw: Analysis(), planner=accepted_plan, simulator=execute)
    assert report["raw_selected"] == 7 and report["overlap_excluded"] == 5
    assert report["decided"] == report["ambiguous"] == report["decided_ambiguous"] == 2
    assert report["decided_unambiguous"] == 0
    assert report["win_rate_lower_pct"] == 0 and report["win_rate_upper_pct"] == 100


def test_history_bi_missing_session_is_unavailable_not_a_zero_signal_result():
    bars = daily_bars()
    window = studied_window(bars)
    missing = bars.pop(92)
    report = replay.replay_asset(bars, "TEST", "long", **window,
        analyzer=lambda *a, **kw: pytest.fail("incomplete history must not be consumed"))
    assert report["status"] == "unavailable_session_coverage"
    assert report["missing_sessions"] == [missing["date"]]
    assert report["win_rate_lower_pct"] is None


def test_history_bi_insufficient_profile_warmup_is_not_claimed_production_parity():
    bars = daily_bars(55)
    report = replay.replay_asset(bars, "TEST", "long", **studied_window(bars, start=50))
    assert report["status"] == "insufficient_90_session_warmup"


def raw_daily(day, **changes):
    midnight = datetime.fromisoformat(day).replace(tzinfo=ZoneInfo("America/New_York"))
    return {"t": midnight.timestamp() * 1000, "o": 100., "h": 101., "l": 99.,
            "c": 100., "v": 1_000_000., **changes}


def test_history_bi_future_candle_prices_cannot_influence_censored_source():
    result = replay.canonical_daily_bars([raw_daily("2026-10-01"), raw_daily("2026-10-02", c=None, v=None)])
    assert len(result) == 1 and result[0]["date"] == "2026-10-01"


@pytest.mark.parametrize("row", [raw_daily("2026-10-01", v=None),
    raw_daily("2026-10-01", c=500), raw_daily("2026-10-01", t=12345),
    raw_daily("2026-09-26"), raw_daily("2026-10-01", t=1790870400123)])
def test_history_bi_bad_closed_history_is_not_repaired(row):
    with pytest.raises(ValueError, match="bi_history_"):
        replay.canonical_daily_bars([row])


def test_history_bi_source_hash_and_adjustment_are_verified(tmp_path):
    rows = [raw_daily("2026-10-01")]
    source = {"symbol": "AAPL", "venue": "us_equity_polygon", "multiplier": 1,
              "span": "day", "adjusted": True, "bars": rows,
              "bars_sha256": replay.fingerprint(rows)}
    path = tmp_path / "stock-AAPL-1day.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    assert replay.load_source(path, "AAPL")[0][0]["date"] == "2026-10-01"
    source["bars"][0]["c"] = 100.5
    path.write_text(json.dumps(source), encoding="utf-8")
    with pytest.raises(ValueError, match="hash_mismatch"):
        replay.load_source(path, "AAPL")


def test_history_bi_actual_simulator_retains_fee_slippage_and_ambiguity():
    bars = [{"date": "2026-07-02", "open": 100., "high": 108., "low": 95., "close": 101.}]
    result = replay._simulate_bi_plan_daily(bars, 0, accepted_plan(), "long", horizon_bars=1)
    assert result["entry_filled"] and result["roundtrip_fee_pct"] == .2
    assert result["exit_slippage_fraction"] == .001
    assert result["actual_entry"] == pytest.approx(100.1)
    assert result["intrabar_ambiguous"] and result["pnl_pct"] < 0
    assert result["pnl_pct_upper"] > result["pnl_pct"]


def test_history_bi_all_fixed_assets_remain_present_even_when_source_unavailable(tmp_path):
    manifest = {"stock_symbols": list(replay.SYMBOLS), "window": {
        "start_inclusive": "2026-07-02T00:00:00Z", "end_exclusive": "2026-10-02T00:00:00Z"}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    report = replay.build_report(tmp_path)
    assert [(row["symbol"], row["direction"]) for row in report["assets"]] == [
        (symbol, direction) for symbol in replay.SYMBOLS for direction in ("long", "short")]
    assert all(row["status"] == "source_unavailable" and row["raw_selected"] is None for row in report["assets"])
    assert report["no_mail"] and report["no_orders"] and report["offline"]
    assert "nicht berechenbar" in replay.render_markdown(report)
