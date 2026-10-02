"""Independent stock producer counterexamples; all providers/state are synthetic."""
from copy import deepcopy

import pytest
import api

from test_cross_scanner_reaudit_regressions import grouped_reply
from test_scanner_reaudit_regressions import Response, NOW, freeze, setup_orb
from test_stock_audit_repair_contracts import daily_bars


@pytest.mark.parametrize("ticker", ["EQTY.B", "LONGCS"])
def test_turtle_reference_confirmed_common_stock_class_reaches_real_pattern_check(monkeypatch, ticker):
    freeze(monkeypatch, NOW.replace(hour=9, minute=0))
    bars = daily_bars(30, session="2026-09-29")
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3_000_000.)
    completed = {key: bars[-1][key] for key in ("o", "h", "l", "c", "v")}
    requested, saved = [], []

    def provider(url, **kwargs):
        grouped = grouped_reply(url, bars, tickers=(ticker,))
        if grouped is not None:
            return grouped
        if "/aggs/ticker/" in url:
            requested.append(url.split("/ticker/")[1].split("/")[0])
            return Response({"status": "OK", "adjusted": True, "results": deepcopy(bars)})
        return Response({"tickers": [{"ticker": ticker, "day": completed,
                                      "prevDay": completed, "lastTrade": {"p": 100.1}}]})

    monkeypatch.setattr(api, "rate_limited_get", provider)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({ticker}, "verified_fixture"))
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    api._turtle_scan_wrapper()
    assert requested == [ticker], "Reference-confirmed CS must not be rejected merely by spelling"
    assert [row["Ticker"] for row in saved] == [ticker]


@pytest.mark.parametrize("short", [False, True])
def test_orb_raw_current_observation_does_not_round_back_onto_opening_range(monkeypatch, short):
    # Real snapshot trade observations can have more decimals than order ticks.
    # Keep order entry/stop/targets tick-rounded, but do not rewrite market evidence.
    raw_current = 99.9999 if short else 100.0001
    saved, sends, _ = setup_orb(monkeypatch, short=short, current=raw_current)
    api._orb_scanner_wrapper()
    row = saved[0]["breakouts"][0]
    assert row["current_price"] == raw_current
    assert "orb_current_breakout_lost" not in api._orb_signal_gate_reasons(row, as_of=NOW)
    assert not sends


@pytest.mark.parametrize("ticker", ["EQTY.B", "LONGCS"])
def test_orb_reference_confirmed_share_class_not_dropped_before_asset_reference(monkeypatch, ticker):
    saved, sends, _ = setup_orb(monkeypatch, short=False)
    original_provider = api.rate_limited_get
    original_grouped = api.fetch_grouped_daily

    def provider(url, **kwargs):
        reply = original_provider(url, **kwargs)
        if isinstance(reply.payload, dict) and "tickers" in reply.payload:
            reply = Response(deepcopy(reply.payload), reply.status_code)
            for observation in reply.payload["tickers"]:
                observation["ticker"] = ticker
        return reply

    def grouped(*args):
        rows = original_grouped(*args)
        return {ticker: rows["AUDT"]} if "AUDT" in rows else rows

    monkeypatch.setattr(api, "rate_limited_get", provider)
    monkeypatch.setattr(api, "fetch_grouped_daily", grouped)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({ticker}, "verified_fixture"))
    api._orb_scanner_wrapper()
    assert [row["ticker"] for row in saved[0]["breakouts"]] == [ticker]
    assert not sends


def test_bear_flag_public_results_use_own_strategy_contract_not_dedicated_bear_scanner(monkeypatch):
    freeze(monkeypatch)
    strategy = "Bear Flag"
    decorated = []
    metadata = {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION, "strategy": strategy,
                "diagnostics": {"strategy": strategy, "coverage": "complete", "final_results": 0}}
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: ([], NOW.isoformat(), metadata, False))
    monkeypatch.setattr(api, "_apply_scanner_visibility_policy", lambda scanner, rows: rows)

    def decorate(rows, scanner, *args, **kwargs):
        decorated.append(scanner)
        return rows

    monkeypatch.setattr(api, "_decorate_scan_results", decorate)
    api.get_scan_results(strategy, None, "stocks")
    assert decorated == ["strategy_scan"], "Bear Flag is not the independent Bear risk/short scanner"


def _bear_daily_history_fixture(monkeypatch, *, latest_session="2026-09-29", ticker="AUDT", snapshot_mutator=None):
    """No live providers, cache writes, mail, audit writes or dedupe claims."""
    freeze(monkeypatch)
    from test_context_scanner_repair import bear_dependencies
    bear_dependencies(monkeypatch)
    monkeypatch.setattr(api, "INVERSE_ETFS", {})
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({ticker}, "verified_fixture"))
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, **kw: row)
    monkeypatch.setattr(api, "_fetch_bear_latest_intraday_state", lambda *a, **kw: {})
    monkeypatch.setattr(api, "_classify_crash_alert_candidate", lambda *a, **kw: {"alertable_now": False})
    monkeypatch.setattr(api, "_crash_alert_suppression_summary_for_rows", lambda *a, **kw: "offline_fixture")
    monkeypatch.setattr(api, "_bear_entry_quality", lambda row: "WAIT")
    monkeypatch.setattr(api, "_bear_short_rule_reasons", lambda row: ["offline_fixture"])
    monkeypatch.setattr(api, "_bear_crash_alert_ok", lambda row: False)
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *a, **kw: {"alertable_now": False})
    monkeypatch.setattr(api, "_alert_suppression_summary_for_rows", lambda *a, **kw: "offline_fixture")
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **kw: pytest.fail("No mail is authorized by this fixture"))
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **kw: pytest.fail("No persistent dedupe writes"))
    bars = daily_bars(60, session=latest_session)
    for bar in bars:
        bar.update(o=100.0, h=101.0, l=99.0, c=100.0, v=1_000_000.0)
    bars[-1].update(o=81.0, h=82.0, l=79.0, c=80.0, v=2_000_000.0)
    snapshot = {"ticker": ticker, "day": {"o": 80.0, "h": 81.0, "l": 69.0, "c": 70.0, "v": 3_000_000.0},
                "prevDay": {key: bars[-1][key] for key in ("o", "h", "l", "c", "v")},
                "lastTrade": {"p": 70.0}}
    if snapshot_mutator is not None:
        snapshot_mutator(snapshot)
    def provider(url, **kw):
        if "/aggs/" in url:
            return Response({"results": list(reversed(deepcopy(bars)))})
        return Response({"status": "OK", "tickers": [deepcopy(snapshot)]})
    monkeypatch.setattr(api, "rate_limited_get", provider)
    captures, saved = [], []
    def capture_plan(**kw):
        captures.append(kw)
        return None
    monkeypatch.setattr(api, "_build_bear_structure_trade_setup", capture_plan)
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    return bars, captures, saved


def test_bear_history_ending_previous_session_keeps_latest_completed_bar_in_baselines(monkeypatch):
    bars, captures, saved = _bear_daily_history_fixture(monkeypatch)
    api._bear_scan_wrapper()
    assert len(captures) == 1
    assert captures[0]["ma20"] == pytest.approx(sum(bar["c"] for bar in bars[-20:]) / 20.0)
    assert captures[0]["low_20d"] == min(bar["l"] for bar in bars[-20:])
    assert saved[0]["breakdown_stocks"][0]["rvol"] == pytest.approx(
        api._project_us_equity_rvol(3_000_000.0 / (sum(bar["v"] for bar in bars[-20:]) / 20.0)))


def test_bear_crash_batch_does_not_override_verified_common_stock_by_suffix(monkeypatch):
    _, _, saved = _bear_daily_history_fixture(monkeypatch, ticker="AUDTQS")
    monkeypatch.setattr(api, "_classify_crash_alert_candidate", lambda *a, **kw: {"alertable_now": True})
    # Reject an isolated claim in-memory before any outbox/mail path.
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **kw: False)
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda scanner, counts: suppressed.append(dict(counts)))
    api._bear_scan_wrapper()
    assert saved[0]["breakdown_stocks"][0]["asset_check"] == "common_stock"
    counts = {key: value for group in suppressed for key, value in group.items()}
    assert "non_common_stock_product" not in counts
    assert counts.get("dedupe_claim_not_owned") == 1


def test_bear_plan_keeps_first_counterbarrier_instead_of_skipping_for_rr():
    setup = api._build_bear_structure_trade_setup(
        entry=100.0, day_high=103.0, day_low=99.0, day_open=102.0,
        ma20=103.0, ma50=104.0, low_20d=98.0, low_60d=95.0, change_pct=-4.0)
    # TP1 is the nearest actual support, even when its R:R later fails a filter.
    assert setup is None or setup["TP1"] == 99.0


def test_bear_plan_without_invalidation_does_not_fabricate_risk_stop():
    setup = api._build_bear_structure_trade_setup(
        entry=100.0, day_high=150.0, day_low=99.0, day_open=140.0,
        ma20=145.0, ma50=150.0, low_20d=98.0, low_60d=95.0, change_pct=-4.0)
    assert setup is None


def test_bear_plan_does_not_fabricate_targets_from_desired_rr():
    setup = api._build_bear_structure_trade_setup(
        entry=100.0, day_high=105.0, day_low=100.0, day_open=104.0,
        ma20=104.5, ma50=105.0, low_20d=101.0, low_60d=101.0, change_pct=-4.0)
    assert setup is None or "measured_move_fallback" not in (setup["tp1_source"], setup["tp2_source"])


def test_bear_mail_rejects_near_first_support_instead_of_pretending_farther_target():
    setup = api._build_bear_structure_trade_setup(
        entry=17.33, day_high=18.15, day_low=16.72, day_open=17.95,
        ma20=18.05, ma50=19.20, low_20d=15.20, low_60d=13.40, change_pct=-6.5)
    assert setup is not None and setup["TP1"] == 16.72
    row = dict(ticker="FWRD", grade="S", score=88, rvol=2.1, price=17.33, direction="SHORT",
               change_pct=-6.5, open_to_current_pct=-4.0, close_pos=.18,
               latest_bar_change_pct=-.2, latest_bar_close_pos=.18, **setup)
    assert api._alert_trade_levels(row)["valid"] is True
    assert api._classify_alert_candidate("bear", row)["alertable_now"] is False


@pytest.mark.parametrize("key,value", [("entry", float("nan")), ("day_high", float("inf")),
                                       ("day_open", None), ("day_low", True), ("ma20", float("nan"))])
def test_bear_native_plan_rejects_invalid_structure_values(key, value):
    inputs = dict(entry=100., day_high=103., day_low=97., day_open=102.,
                  ma20=103., ma50=104., low_20d=95., low_60d=90., change_pct=-4.)
    inputs[key] = value
    assert api._build_bear_structure_trade_setup(**inputs) is None


def test_bear_native_plan_uses_exact_tick_grid_geometry_after_rounding():
    setup = api._build_bear_structure_trade_setup(
        entry=100.0001, day_high=103.0001, day_low=97.0049, day_open=102.0001,
        ma20=103., ma50=104., low_20d=95.0049, low_60d=90., change_pct=-4.)
    assert setup is not None
    expected = api.trade_geometry(setup["Entry"], setup["StopLoss"], setup["TP1"], setup["TP2"], "SHORT")
    assert setup["rr"] == expected["rr"]
    assert setup["rr_tp1"] == expected["rr_tp1"]
    assert setup["rr_tp2"] == expected["rr_tp2"]


def test_old_bear_v1_cached_plan_cannot_keep_pre_repair_mail_release(monkeypatch):
    # Legacy v1 skipped the real near support (16.72) to make 15.20/13.40
    # look like the first targets. A current quote cannot repair that evidence.
    monkeypatch.setattr(api, "_load_common_stock_universe_cached", lambda **kw: ({"FWRD"}, "offline_fixture"))
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **kw: 0)
    monkeypatch.setattr(api, "_bearish_stock_alert_remaining", lambda *a, **kw: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    row = dict(ticker="FWRD", grade="S", score=88, rvol=2.1, price=17.33,
               direction="SHORT", change_pct=-6.5, open_to_current_pct=-4.0,
               close_pos=.18, latest_bar_change_pct=-.2, latest_bar_close_pos=.18,
               Entry=17.33, StopLoss=18.04, TP1=15.20, TP2=13.40,
               level_model="bear_structure_first_v1", trade_setup_source="native_bear_structure",
               stop_source="day open rejection", tp1_source="20D low", tp2_source="60D low")
    state = api._classify_alert_candidate("bear", row, now=NOW.timestamp(), cache_only=True)
    assert state["alertable_now"] is False, state
    assert "bear_plan_contract_invalid" in state["suppression_reasons"]


@pytest.mark.parametrize("source", ["native_bear_structure", "native_bear_vrvp_structure"])
def test_new_bear_v2_native_plan_can_pass_same_final_mail_gate(monkeypatch, source):
    monkeypatch.setattr(api, "_load_common_stock_universe_cached", lambda **kw: ({"FWRD"}, "offline_fixture"))
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **kw: 0)
    monkeypatch.setattr(api, "_bearish_stock_alert_remaining", lambda *a, **kw: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    setup = api._build_bear_structure_trade_setup(
        entry=17.33, day_high=18.15, day_low=15.80, day_open=17.95,
        ma20=18.05, ma50=19.20, low_20d=15.20, low_60d=13.40, change_pct=-6.5)
    row = dict(ticker="FWRD", grade="S", score=88, rvol=2.1, price=17.33,
               direction="SHORT", change_pct=-6.5, open_to_current_pct=-4.0,
               close_pos=.18, latest_bar_change_pct=-.2, latest_bar_close_pos=.18,
               **setup)
    row["trade_setup_source"] = source
    assert api._classify_alert_candidate("bear", row, now=NOW.timestamp(), cache_only=True)["alertable_now"] is True


@pytest.mark.parametrize("field,reason", [
    ("close_pos", "not_closing_near_low"),
    ("change_pct", "drop_too_extended_no_chase"),
    ("open_to_current_pct", "current_candle_green_reclaim"),
])
def test_bear_producer_keeps_raw_snapshot_at_final_short_gate(monkeypatch, field, reason):
    real_gate = api._bear_short_rule_reasons
    def mutate(snapshot):
        day = snapshot["day"]
        if field == "close_pos":
            day.update(o=71., h=69.+1./.4504, l=69., c=70.)
        elif field == "change_pct":
            day.update(o=73., h=75., l=69., c=80.*(1.-.119996))
        else:
            day.update(o=70./1.002001, h=71., l=69., c=70.)
    _, _, saved = _bear_daily_history_fixture(monkeypatch, snapshot_mutator=mutate)
    api._bear_scan_wrapper()
    row = saved[0]["breakdown_stocks"][0]
    expected = {"close_pos": .4504, "change_pct": -11.9996, "open_to_current_pct": .2001}[field]
    assert row[field] == pytest.approx(expected, abs=1e-11)
    raw_reasons = real_gate(row)
    assert (reason in raw_reasons) is (field != "change_pct"), raw_reasons
