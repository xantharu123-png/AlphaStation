"""Gap/BI regression probes with synthetic data and independent mathematics.

Run through tmp/offline_mail_fix_tests_20260925.py. Assertions express the
required invariant, not a newly relaxed scanner rule. The BI pipeline
probes inject 17 checks to isolate the subsequent data/publication contract;
they do not claim the synthetic flat series is itself a real 17/20 setup.
"""
import json
import random
import copy
from datetime import date, datetime, timezone

import pytest

import api
from modules import scanners, stock_swing_contract as swing
from modules.indicators import calculate_adx
from test_bi_deep_fixes_scan import _attach_ts, _flat_bars, _to_polygon
from test_bi_diagnostics_integration import _io, _read, _result
from test_bi_market_data import _Reply, _lifecycle
from test_gap_momentum_math_audit_20260930 import _gap_fixture, REAL_METRICS

NOW = datetime(2026, 9, 30, 0, tzinfo=timezone.utc)
REAL_NATIVE_ENRICHMENT = api._enrich_stock_strategy_native_plan


def freeze_bi(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)
    monkeypatch.setattr(scanners, "datetime", Clock)
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")


def accepted_plan(*args, **kwargs):
    if kwargs.get("direction") == "short":
        return dict(accepted=True, Entry=99., StopLoss=101., TP1=95., TP2=93.,
                    RiskReward=2., RangeHigh=101., RangeLow=99.)
    return dict(accepted=True, Entry=101., StopLoss=99., TP1=105., TP2=107.,
                RiskReward=2., RangeHigh=101., RangeLow=99.)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_bi_qualified_candidate_is_not_deleted_for_plan_warning(monkeypatch, tmp_path, direction):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17))
    monkeypatch.setattr(scanners, "build_bi_trade_plan", lambda *a, **kw: {
        "accepted": False, "reason": "structural_barrier_blocked",
        "Entry": 101., "StopLoss": 99., "TP1": 101.5, "TP2": 104.,
        "structure_status": "WAIT_BREAK_RECLAIM",
    })
    scanners._bi_background_scan("fixture", direction, tickers)
    result = _read(tmp_path, direction)
    assert result["diagnostics"]["indicator_passed"] == 1
    # A 17/20 candidate with a plan warning is visible, never actionable mail.
    assert len(result["results"]) == 1, result["diagnostics"]["rejected"]
    row = result["results"][0]
    assert row["BI_PlanAccepted"] is False
    assert row["visibility_is_trade_signal"] is False
    assert row["native_plan_reason"] == "structural_barrier_blocked"


@pytest.mark.parametrize("direction", ["long", "short"])
def test_bi_bonus_movers_do_not_bypass_reference_asset_membership(monkeypatch, tmp_path, direction):
    _io(monkeypatch, tmp_path, _result(17))
    freeze_bi(monkeypatch)
    raw = _to_polygon(_attach_ts(_flat_bars(), end_day=date(2026, 9, 29)))
    requested = []

    def provider(url, **kwargs):
        if "/v3/reference/tickers" in url:
            assert kwargs["params"]["type"] == "CS"
            return _Reply({"status": "OK", "results": [
                {"ticker": "EQTY", "type": "CS", "name": "Fixture Common Stock"},
                {"ticker": "EQTY.B", "type": "CS", "name": "Fixture Class B Common Stock"},
            ]})
        if url.endswith("/gainers"):
            return _Reply({"tickers": [{"ticker": "FETF", "type": "ETF", "name": "Fixture ETF"}]})
        if url.endswith("/losers"):
            return _Reply({"tickers": []})
        ticker = url.split("/ticker/")[1].split("/")[0]
        requested.append(ticker)
        return _Reply({"results": raw})

    monkeypatch.setattr(scanners, "rate_limited_get", provider)
    monkeypatch.setattr(scanners, "build_bi_trade_plan", accepted_plan)
    scanners._bi_background_scan("fixture", direction, None)
    cache = _read(tmp_path, direction)
    assert "FETF" not in requested, {"analyzed": requested, "raw_rows": [r["Ticker"] for r in cache["results"]]}


@pytest.mark.parametrize("direction", ["long", "short"])
def test_bi_reference_confirmed_share_class_is_not_rejected_by_dot(monkeypatch, tmp_path, direction):
    _io(monkeypatch, tmp_path, _result(17))
    freeze_bi(monkeypatch)
    raw = _to_polygon(_attach_ts(_flat_bars(), end_day=date(2026, 9, 29)))
    requested = []
    def provider(url, **kwargs):
        if "/v3/reference/tickers" in url:
            return _Reply({"status": "OK", "results": [
                {"ticker": "EQTY", "type": "CS"}, {"ticker": "EQTY.B", "type": "CS"},
            ]})
        if "/snapshot/" in url:
            return _Reply({"tickers": []})
        requested.append(url.split("/ticker/")[1].split("/")[0])
        return _Reply({"results": raw})
    monkeypatch.setattr(scanners, "rate_limited_get", provider)
    monkeypatch.setattr(scanners, "build_bi_trade_plan", accepted_plan)
    scanners._bi_background_scan("fixture", direction, None)
    assert "EQTY.B" in requested, requested


def test_bi_cumulative_pump_uses_compounded_two_day_return(monkeypatch, tmp_path):
    bars = _flat_bars()
    bars[-3].update(open=100., high=100.6, low=99.4, close=100.)
    bars[-2].update(open=100., high=106., low=99.9, close=105.9)
    bars[-1].update(open=105.9, high=112.3, low=105.8, close=112.1481)
    payload = {"results": _to_polygon(_attach_ts(bars, end_day=date(2026, 9, 29)))}
    scanner, tickers, final, _, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [payload],
    )
    freeze_bi(monkeypatch)
    monkeypatch.setattr(scanner, "build_bi_trade_plan", accepted_plan)
    scanner._bi_background_scan("fixture", "long", tickers)
    cache = json.loads(final.read_text())
    assert (bars[-1]["close"] / bars[-3]["close"] - 1) * 100 > 12.
    assert cache["diagnostics"]["rejected"].get("cumulative_pump") == 1, {
        "true_return_pct": 12.1481, "sum_of_daily_percentages": 11.8,
        "analyzed": analyses, "final_rows": len(cache["results"]),
    }


def test_bi_stale_history_cannot_create_current_completed_scan_row(monkeypatch, tmp_path):
    payload = {"results": _to_polygon(_attach_ts(_flat_bars(), end_day=date(2026, 9, 21)))}
    scanner, tickers, final, _, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [payload],
    )
    freeze_bi(monkeypatch)
    monkeypatch.setattr(scanner, "build_bi_trade_plan", accepted_plan)
    scanner._bi_background_scan("fixture", "long", tickers)
    cache = json.loads(final.read_text())
    assert all(swing.validate(row, NOW) for row in cache["results"]), {
        "expected_session": "2026-09-29",
        "cached_sessions": [row["swing_analysis_session"] for row in cache["results"]],
        "coverage": cache["diagnostics"]["coverage"], "analyzed": analyses,
    }
    assert analyses == [] and cache["results"] == []
    assert cache["diagnostics"]["coverage"] == "complete_with_exclusions"
    assert cache["diagnostics"]["data_error_counts"] == {"stale_daily_history": 1}


def reference_adx(bars, period=14):
    """Wilder: first DX uses the initial sum of the first 14 TR/DM values."""
    moves = []
    for previous, bar in zip(bars, bars[1:]):
        up = bar["high"] - previous["high"]
        down = previous["low"] - bar["low"]
        moves.append((max(bar["high"]-bar["low"], abs(bar["high"]-previous["close"]),
                          abs(bar["low"]-previous["close"])),
                      up if up > down and up > 0 else 0.,
                      down if down > up and down > 0 else 0.))
    smooth = [sum(item[k] for item in moves[:period]) for k in range(3)]
    dx = []
    for index in range(period - 1, len(moves)):
        if index >= period:
            smooth = [value - value / period + moves[index][k] for k, value in enumerate(smooth)]
        plus, minus = smooth[1:]
        dx.append(100. * abs(plus-minus) / (plus+minus) if plus+minus else 0.)
    values = [sum(dx[:period]) / period]
    for value in dx[period:]:
        values.append((values[-1]*(period-1)+value)/period)
    return values[-1], values[-6] if len(values) >= 6 else None


def adx_bars(seed):
    rng = random.Random(seed)
    result, last = [], 100.
    for _ in range(50):
        current = last + rng.uniform(-1., 1.)
        result.append(dict(open=last, close=current, high=max(last, current)+rng.uniform(0., .8),
                           low=min(last, current)-rng.uniform(0., .8), volume=1_000_000))
        last = current
    return result


def test_adx_seed_preserves_direction_of_bi_turning_vote():
    mismatches = []
    for seed in range(200):
        bars = adx_bars(seed)
        actual = calculate_adx(bars, raw=True)
        expected = reference_adx(bars)
        actual_vote = actual[0] < 25 and actual[0] > actual[1]
        expected_vote = expected[0] < 25 and expected[0] > expected[1]
        if actual_vote != expected_vote:
            mismatches.append(dict(seed=seed, actual=actual, expected=expected,
                                   actual_green=actual_vote, expected_green=expected_vote))
    print("ADX_COMPARISON=" + json.dumps({"sequences": 200, "vote_mismatches": len(mismatches), "examples": mismatches[:6]}))
    assert not mismatches, mismatches[:6]


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("gap,qualifies", [(2.999, False), (3., True), (3.001, True)])
def test_gap_threshold_uses_actual_open_gap(monkeypatch, direction, gap, qualifies):
    name = _gap_fixture(monkeypatch, direction)
    observation = api._fetch_strategy_snapshot_universe(name)[0]
    observation["day"]["o"] = 100. + gap * (1 if direction == "Long" else -1)
    history = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    history[-1]["open"] = observation["day"]["o"]
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: history)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", REAL_METRICS)
    rows = api._strategy_scan_wrapper(name, send_email=False)
    assert bool(rows) is qualifies


def test_two_swiss_daily_gap_slots_use_same_completed_session():
    # No new regular-session gap exists between these two runs. This is a
    # deliberate completed-1D scan, not a premarket-gap prediction.
    assert swing.completed_sessions(datetime(2026, 9, 30, 0, tzinfo=timezone.utc), 1) == ["2026-09-29"]
    assert swing.completed_sessions(datetime(2026, 9, 30, 10, tzinfo=timezone.utc), 1) == ["2026-09-29"]


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_gap_cannot_mix_a_reference_price_with_a_different_history_close(monkeypatch, direction):
    name = _gap_fixture(monkeypatch, direction)
    history = copy.deepcopy(api._fetch_strategy_daily_history("TEST", 70, {}, True))
    # Both inputs are individually valid, dated OHLCV; one provider observation
    # contradicts the other. The final candle here is not the $105/$95 snapshot.
    history[-1].update(open=80., high=82., low=79., close=81.)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: history)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", REAL_METRICS)
    monkeypatch.setattr(api, "_enrich_stock_strategy_native_plan", REAL_NATIVE_ENRICHMENT)
    try:
        rows = api._strategy_scan_wrapper(name, send_email=False)
    except api.ScannerDataError as error:
        assert error.code in {"scan_data_invalid", "scan_data_incomplete"}
        return
    assert not rows, {"history_close": 81., "row_price": rows[0]["price"],
                      "row_rsi": rows[0].get("RSI"), "History_OK": rows[0].get("History_OK"),
                      "plan_reason": rows[0].get("native_plan_reason")}


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_gap_reference_confirmed_share_class_is_not_rejected_by_dot(monkeypatch, direction):
    name = _gap_fixture(monkeypatch, direction)
    observation = api._fetch_strategy_snapshot_universe(name)[0]
    # The real common-stock check would accept this reference-confirmed type;
    # the earlier string check must not make that decision unreachable.
    observation["ticker"] = "EQTY.B"
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"EQTY.B"}, "fixture"))
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", REAL_METRICS)
    try:
        rows = api._strategy_scan_wrapper(name, send_email=False)
    except api.ScannerDataError as error:
        pytest.fail(f"Confirmed common share class was never priced: {error.code}, {error.diagnostics}")
    assert len(rows) == 1 and rows[0]["ticker"] == "EQTY.B"


@pytest.mark.parametrize("length", [28, 33, 36, 50, 150])
@pytest.mark.parametrize("seed", [25, 100, 153, 42])
def test_adx_numeric_reference_and_scaling(length, seed):
    bars = (adx_bars(seed) * 3)[:length]
    expected = reference_adx(bars)
    actual = calculate_adx(bars, raw=True)
    assert actual[0] == pytest.approx(expected[0], abs=1e-10)
    assert actual[1] == (None if expected[1] is None else pytest.approx(expected[1], abs=1e-10))
    scaled = [{k: v * 1000 if k != "volume" else v for k, v in bar.items()} for bar in bars]
    scaled_actual = calculate_adx(scaled, raw=True)
    assert scaled_actual[0] == pytest.approx(expected[0], abs=1e-10)
    assert scaled_actual[1] == (None if expected[1] is None else pytest.approx(expected[1], abs=1e-10))


@pytest.mark.parametrize("value", [None, True, float("nan"), float("inf"), -1., 0.])
def test_adx_bad_evidence_remains_unknown(value):
    bars = adx_bars(1)
    bars[5]["close"] = value
    assert calculate_adx(bars) == (None, None)


def test_adx_flat_and_one_direction_have_known_values():
    flat = [dict(open=100., high=100., low=100., close=100.) for _ in range(50)]
    assert calculate_adx(flat) == (0., 0.)
    rising = [dict(open=100.+i, high=101.+i, low=99.+i, close=100.+i) for i in range(50)]
    falling = [dict(open=200.-i, high=201.-i, low=199.-i, close=200.-i) for i in range(50)]
    assert calculate_adx(rising) == calculate_adx(falling) == (100., 100.)


@pytest.mark.parametrize("source,key,expected", [
    ("current", "open", "reference_price_mismatch"),
    ("current", "high", "reference_price_mismatch"),
    ("current", "low", "reference_price_mismatch"),
    ("current", "close", "reference_price_mismatch"),
    ("current", "volume", "reference_volume_mismatch"),
    ("previous", "close", "reference_price_mismatch"),
])
def test_daily_reference_covers_every_input_used_by_gap(monkeypatch, source, key, expected):
    name = _gap_fixture(monkeypatch)
    observation = api._fetch_strategy_snapshot_universe(name)[0]
    bars = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    assert swing.history_observation_error(bars, observation, previous_session="2026-09-28") is None
    bars[-1 if source == "current" else -2][key] *= 1.01
    assert swing.history_observation_error(bars, observation, previous_session="2026-09-28") == expected


@pytest.mark.parametrize("which,expected", [("current", "history_not_current"),
                                            ("previous", "previous_session_missing")])
def test_daily_reference_requires_both_actual_sessions(monkeypatch, which, expected):
    name = _gap_fixture(monkeypatch)
    observation = api._fetch_strategy_snapshot_universe(name)[0]
    bars = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    bars.pop(-1 if which == "current" else -2)
    assert swing.history_observation_error(bars, observation, previous_session="2026-09-28") == expected


@pytest.mark.parametrize("ticker", ["EQTY.B", "LONGNAMEW", "CLASS.1"])
def test_reference_proof_not_symbol_suffix_controls_asset_admission(monkeypatch, ticker):
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "names", {})
    assert api._stock_alert_asset_exclusion_reason(ticker, {ticker}, "fixture") is None
    assert api._stock_alert_asset_exclusion_reason(ticker, set(), "fixture") is not None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_bi_plan_warning_is_visible_but_cannot_become_mail(monkeypatch, direction):
    from test_bi_signal_contract_downstream import _bi_row
    row = _bi_row()
    row.update(accepted_plan(direction=direction))
    row.update(Preis=100., RVOL=2., BI_PlanAccepted=False, BI_Direction=direction.upper(),
               native_plan_status="unavailable", native_plan_reason="structural_barrier_blocked")
    scanner = "bi_" + direction
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"VALID"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 99.)
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda *a: [])
    monkeypatch.setattr(api, "_stock_swing_short_rule_reasons", lambda *a: [])
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda *a: [])
    monkeypatch.setattr(api, "_structural_barrier_alert_reason", lambda *a: None)
    state = api._classify_alert_candidate(scanner, row)
    assert state["alertable_now"] is False
    assert "bi_plan_not_released" in state["suppression_reasons"]
    visible = api._apply_scanner_visibility_policy(scanner, [row])
    assert len(visible) == 1 and visible[0]["visibility_status"] == "candidate_warning"
    assert visible[0]["visibility_is_trade_signal"] is False


def test_old_bi_arithmetic_cannot_be_relabelled_as_current():
    from test_bi_signal_contract_downstream import _bi_row
    row = _bi_row()
    row["BI_IndicatorContractVersion"] = "stock-bi-20-v3"
    assert api._filter_bi_signal_rows("bi_long", [row]) == []


def test_warning_candidates_cannot_crowd_out_a_later_valid_bi_plan(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17), count=51)
    plans = iter([dict(accepted=False, reason="structural_barrier_blocked") for _ in range(50)]
                 + [accepted_plan(direction="long")])
    monkeypatch.setattr(scanners, "build_bi_trade_plan", lambda *a, **kw: next(plans))
    scanners._bi_background_scan("fixture", "long", tickers)
    rows = _read(tmp_path)["results"]
    assert len(rows) == 51
    assert [row["Ticker"] for row in rows if row["BI_PlanAccepted"]] == [tickers[-1]]


def test_warning_bi_candidate_cannot_be_recorded_as_a_sent_trade(monkeypatch):
    from test_bi_signal_contract_downstream import _bi_row
    row = {**_bi_row(), "BI_PlanAccepted": False}
    monkeypatch.setattr(api, "record_alert_signals", lambda *a, **kw: pytest.fail("Warning reached tracker"))
    api._safe_record_alert_signals("bi_long", [row], delivery_recipient_keys=("fixture",))


@pytest.mark.parametrize("direction", ["long", "short"])
def test_warning_plan_is_rejected_again_before_final_revalidation_io(monkeypatch, direction):
    from test_bi_signal_contract_downstream import _bi_row
    row = {**_bi_row(), **swing.metadata("2026-09-29", 100.), "BI_PlanAccepted": False}
    monkeypatch.setattr(api, "_scanner_uses_swing_horizon", lambda *a: True)
    monkeypatch.setattr(api, "_alert_trade_levels", lambda *a: pytest.fail("Unapproved plan reached final level evaluation"))
    result = api._revalidate_stock_swing_plan(row, now_ts=NOW.timestamp(), scanner_name="bi_"+direction)
    assert result == {"ok": False, "reason": "bi_plan_not_released"}


@pytest.mark.parametrize("seed,indicator_id,expected", [(492, 7, True), (214, 9, False), (63, 14, True)])
def test_bi_decisions_do_not_round_before_threshold_or_crossing(seed, indicator_id, expected):
    from modules import patterns
    from modules.indicators import calculate_rsi_from_bars, calculate_stochastic
    bars = adx_bars(seed)
    # Demonstrate the old false decision using the preserved display interface.
    if indicator_id == 7:
        current, previous = calculate_adx(bars)
        rounded_pass = current < 25 and current > previous
        actual, prior = reference_adx(bars)
        assert (actual < 25 and actual > prior) is expected
    elif indicator_id == 9:
        rounded_pass = 50 <= calculate_rsi_from_bars(bars) <= 65
        closes = [bar["close"] for bar in bars]
        changes = [right-left for left, right in zip(closes, closes[1:])]
        gain = sum(max(change, 0) for change in changes[:14])/14
        loss = sum(max(-change, 0) for change in changes[:14])/14
        for change in changes[14:]:
            gain = (gain*13 + max(change, 0))/14
            loss = (loss*13 + max(-change, 0))/14
        assert (50 <= 100-100/(1+gain/loss) <= 65) is expected
    else:
        k, d = calculate_stochastic(bars)
        rounded_pass = k > d
        values = []
        for end in range(len(bars)-2, len(bars)+1):
            window = bars[end-14:end]
            high, low = max(bar["high"] for bar in window), min(bar["low"] for bar in window)
            values.append(100*(window[-1]["close"]-low)/(high-low))
        assert (values[-1] > sum(values)/3) is expected
    assert rounded_pass is not expected
    result = patterns.analyze_breakout_imminent(bars, direction="long")
    check = next(check for check in result.indicator_checks if check["id"] == indicator_id)
    assert check["available"] is True and check["passed"] is expected


def test_adx_rise_from_zero_is_valid_evidence_not_missing_previous_value(monkeypatch):
    from modules import patterns
    monkeypatch.setattr(patterns, "calculate_adx", lambda *a, **kw: (10., 0.))
    result = patterns.analyze_breakout_imminent(adx_bars(1), direction="long")
    check = next(check for check in result.indicator_checks if check["id"] == 7)
    assert check["passed"] is True and check["points"] == 14


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_gap_one_incoherent_symbol_does_not_delete_coherent_siblings(monkeypatch, direction):
    name = _gap_fixture(monkeypatch, direction)
    base = api._fetch_strategy_snapshot_universe(name)[0]
    history = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    observations = [dict(base, ticker="BAD"), dict(base, ticker="GOOD")]
    bad_history = copy.deepcopy(history)
    bad_history[-1]["volume"] *= 2
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: observations)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda ticker, *a: bad_history if ticker == "BAD" else history)
    written = []
    monkeypatch.setattr(api, "finalize_cache_file", lambda path, rows, **kw: written.append(kw["metadata"]["diagnostics"]))
    rows = api._strategy_scan_wrapper(name, send_email=False)
    assert [row["ticker"] for row in rows] == ["GOOD"]
    assert written[0]["coverage"] == "complete_with_exclusions"
    assert written[0]["excluded_data_symbols"] == 1
    assert written[0]["rejected"]["daily_reference:reference_volume_mismatch"] == 1


def test_gap_widespread_reference_disagreement_preserves_last_final_cache(monkeypatch):
    name = _gap_fixture(monkeypatch)
    base = api._fetch_strategy_snapshot_universe(name)[0]
    history = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    history[-1]["volume"] *= 2
    observations = [dict(base, ticker=f"BAD{index}") for index in range(21)]
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: observations)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *a: history)
    monkeypatch.setattr(api, "finalize_cache_file", lambda *a, **kw: pytest.fail("Replaced final cache during provider disagreement"))
    with pytest.raises(api.ScannerDataError) as caught:
        api._strategy_scan_wrapper(name, send_email=False)
    assert caught.value.code == "scan_data_invalid"
    assert caught.value.diagnostics["coverage"] == "incomplete"


def test_daily_reference_allows_only_representation_noise(monkeypatch):
    name = _gap_fixture(monkeypatch)
    base = api._fetch_strategy_snapshot_universe(name)[0]
    history = api._fetch_strategy_daily_history("TEST", 70, {}, True)
    history[-1]["close"] += 0.000001
    assert swing.history_observation_error(history, base, previous_session="2026-09-28") is None
    assert swing.history_observation_error([*history, history[-1]], base,
                                           previous_session="2026-09-28") == "history_not_current"


@pytest.mark.parametrize("reason", ["invalid_data", "unknown_failure", None])
def test_unknown_or_bad_data_plan_rejection_cannot_be_downgraded_to_warning(monkeypatch, tmp_path, reason):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17))
    monkeypatch.setattr(scanners, "build_bi_trade_plan", lambda *a, **kw: dict(accepted=False, reason=reason))
    scanners._bi_background_scan("fixture", "long", tickers)
    assert _read(tmp_path)["results"] == []


@pytest.mark.parametrize("volume,qualifies", [(1_499_600, False), (1_500_000, True), (1_500_400, True)])
def test_daily_momentum_comparison_uses_the_same_unrounded_rvol_as_gap(volume, qualifies):
    from modules.momentum_daily_backtest import evaluate_daily_momentum
    from test_momentum_backtest_parity import _daily_bars
    bars = _daily_bars()
    bars[30]["volume"] = volume
    selected = evaluate_daily_momentum(bars, 30)
    assert (selected is not None) is qualifies
    if selected:
        assert selected["rvol"] == volume / 1_000_000


def test_strategy_metrics_do_not_round_rsi_before_downstream_decisions():
    from modules.indicators import calculate_rsi_from_bars
    bars = _attach_ts(adx_bars(214), end_day=date(2026, 9, 29))
    last = bars[-1]
    actual = api._strategy_daily_history_metrics(
        bars, price=last["close"], day_open=last["open"], day_high=last["high"],
        day_low=last["low"], day_volume=last["volume"],
        now_utc=NOW, signal_session="2026-09-29", include_structure=False,
    )
    raw = calculate_rsi_from_bars(bars[-40:], raw=True)
    assert actual["rsi14"] == raw
    assert raw != round(raw, 1)
