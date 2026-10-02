"""Offline counterexamples from the 2026-10-02 BI/Biotech/Penny audit."""
from datetime import datetime, timedelta, timezone
import json

import pytest

from modules import scanners
from modules.penny_stock_scanner import _daily_resistance_levels, parse_penny_daily_aggregates
from modules.penny_stock_scanner import analyze_penny_intraday
from test_bi_diagnostics_integration import _result
from test_bi_deep_fixes_scan import _flat_bars, _to_polygon
from test_bi_transport_recovery import Reply, setup


def _edge(news):
    return scanners._calculate_biotech_catalyst_edge(
        {"pipeline_score": 0, "readout_score": 0, "catalyst_readouts": []},
        {"catalyst_score": 0, "negative_flags": [], "news": [news]},
        {"technical_score": 10, "details": {"price": 20, "RVOL": 1.0,
             "rvol_up_day": True, "pos_90d": 60, "range_10d%": 6, "chart_health": 8}},
        {"market_cap_millions": 3000, "shares_millions": 30},
    )


@pytest.mark.parametrize("title", [
    "Acme reports positive data with no safety concerns seen",
    "Acme confirms its trial is not on clinical hold",
    "Acme confirms its trial is not yet on clinical hold",
    "Acme says there was no public offering and no dilution",
])
def test_biotech_edge_respects_negated_risk_news(title):
    # The semantic classifier has not produced a negative flag. The downstream
    # risk layer must not reintroduce an explicitly negated event by substring.
    result = _edge({"title": title, "description": ""})
    assert result["trade_mode"] != "AVOID_NEWS_RISK", result
    assert result["regulatory_risk"] == 0
    assert result["dilution_risk"] == 0


@pytest.mark.parametrize("available", [False, True])
def test_biotech_quick_refresh_drops_unavailable_cached_calendar(monkeypatch, available):
    old = {"Ticker": "TEST", "Score": 70, "Technical_Score": 14,
           "Risk_Score": 10, "Pipeline_Score": 20, "Readout_Score": 15,
           "Readout_Label": "Old PDUFA", "Readout_Details": [
               {"full_label": "Phase 3 PDUFA", "days_until": 12}],
           "BPIQ_Available": True, "BPIQ_Catalysts": [
               {"full_label": "Phase 3 PDUFA", "days_until": 12}],
           "Tech_Details": {"price": 20, "RVOL": 2, "rvol_up_day": True,
                            "pos_90d": 60, "range_10d%": 6, "chart_health": 8},
           "Chart_Health": 8, "RVOL": 2, "MCap_M": 3000, "Shares_M": 30,
           "Catalyst": "Old PDUFA", "Catalyst_Date": "2026-10-08",
           "Event_Result": " Positiv"}
    monkeypatch.setattr(scanners, "_biotech_cache_load", lambda **kwargs: [old])
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kwargs: [])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kwargs: {"NEW"})
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *args, **kwargs: None)
    clocks = []
    def refresh_news(*args, **kwargs):
        clocks.append(kwargs.get("as_of"))
        return {"catalyst_score": 30, "news": [], "negative_flags": [],
                "catalysts": [], "best_catalyst": None, "had_catalyst_keywords": True}
    monkeypatch.setattr(scanners, "_scan_biotech_news", refresh_news)
    monkeypatch.setattr(scanners, "_biotech_news_momentum", lambda *args: {
        "momentum_score": 10, "sentiment_summary": "positive"})
    readouts = [{"full_label": "Phase 3 PDUFA", "days_until": 12,
                 "category": "UPCOMING", "catalyst_date_text": "2026-10-14"}]
    monkeypatch.setattr(scanners, "_get_bpiq_catalysts", lambda *args: {
        "bpiq_available": available, "readout_score": 9 if available else 0,
        "readout_label": "Fresh PDUFA" if available else "",
        "catalyst_readouts": readouts if available else [],
        "provider_status": {"status": "OK" if available else "warning"}})
    saved = []
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda rows, **kwargs: saved.extend(rows))
    scanners._biotech_quick_scan("offline-fixture")
    assert len(clocks) == 2 and clocks[0] is clocks[1] and clocks[0].tzinfo is not None
    assert {row["Ticker"] for row in saved} == {"TEST", "NEW"}
    saved = [row for row in saved if row["Ticker"] == "TEST"]
    assert saved and saved[0]["BPIQ_Available"] is available
    if available:
        assert saved[0]["Pipeline_Score"] == 12 and saved[0]["Readout_Score"] == 9
        assert saved[0]["Readout_Details"] == saved[0]["BPIQ_Catalysts"] == readouts
        assert saved[0]["Catalyst"] == "Fresh PDUFA"
        assert saved[0]["Catalyst_Date"] == "2026-10-14"
        assert saved[0]["Event_Result"] == "📅 Geplant"
    else:
        assert saved[0]["Pipeline_Score"] == saved[0]["Readout_Score"] == 0
        assert saved[0]["Readout_Details"] == saved[0]["BPIQ_Catalysts"] == []
        assert saved[0]["Catalyst"] != "Old PDUFA" and saved[0]["Catalyst_Date"] == ""
        assert saved[0]["Event_Result"] != " Positiv"
    assert old["BPIQ_Available"] is True  # Do not mutate the historic snapshot.


def _penny_daily(day, high):
    return {"t": int(day.timestamp() * 1000), "o": 2, "h": high,
            "l": 1.9, "c": 2, "v": 100000}


def test_penny_daily_resistance_cannot_use_multiple_future_sessions():
    clock = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)
    prior = [_penny_daily(clock - timedelta(days=20-i), 2.2) for i in range(19)]
    future = [_penny_daily(clock + timedelta(days=i), 8+i) for i in range(1, 4)]
    result = _daily_resistance_levels(prior + future, 2, now_ts=clock.timestamp())
    assert result and max(item["price"] for item in result) == 2.2


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("rvol", [0.496, 0.696])
def test_bi_rvol_grade_uses_raw_ratio(monkeypatch, tmp_path, direction, rvol):
    bars = _flat_bars(vol=250000)
    bars[-1]["volume"] = int(250000 * rvol)
    payload = {"status": "OK", "results": _to_polygon(bars)}
    tickers, final, calls, _, _ = setup(
        monkeypatch, tmp_path, [Reply(payload=payload)], direction=direction)
    result = _result(17)
    # Probe the exact existing grade boundary, not the indicator calculation.
    values = tuple(result[:5]) + (("A" if rvol > .5 else "B"),) + tuple(result[6:])
    from modules.patterns import BreakoutAnalysisResult
    qualified = BreakoutAnalysisResult(values, indicator_checks=result.indicator_checks,
        green_count=17, available_count=20, indicator_contract_ok=True)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *args, **kwargs: qualified)
    scanners._bi_background_scan("fixture", direction, tickers)
    row = json.loads(final.read_text())["results"][0]
    assert row["RVOL"] == pytest.approx(rvol)
    assert row["BI_Grade"] == ("B" if rvol > .5 else "C")


def test_penny_bad_closed_bar_cannot_disappear_from_execution_history():
    from test_penny_stock_scanner import _compressed_breakout_bars, _market_now
    now = _market_now()
    bars = _compressed_breakout_bars(now)
    del bars[-2]["open"]
    result = analyze_penny_intraday(bars, now_ts=now)
    assert result["data_ok"] is False
    assert result.get("trigger_confirmed") is not True


def _daily_payload(*bars):
    return {"status": "OK", "adjusted": True, "results": list(bars),
            "resultsCount": len(bars), "queryCount": len(bars)}


def _daily_session(session, *, high=2.2, volume=100000):
    from zoneinfo import ZoneInfo
    timestamp = datetime.fromisoformat(session).replace(tzinfo=ZoneInfo("America/New_York"))
    return {"t": int(timestamp.timestamp() * 1000), "o": 2.0, "h": high,
            "l": 1.9, "c": 2.05, "v": volume}


def test_penny_daily_parser_keeps_price_valid_unknown_volume_without_zero():
    bar = _daily_session("2026-10-01")
    del bar["v"]
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    parsed = parse_penny_daily_aggregates(_daily_payload(bar), as_of=clock)
    assert parsed == [{"open": 2.0, "high": 2.2, "low": 1.9, "close": 2.05,
                       "volume": None, "timestamp": bar["t"]}]
    assert _daily_resistance_levels(parsed, 2, now_ts=clock.timestamp())


@pytest.mark.parametrize("close_hour", [17, 18])
def test_penny_daily_parser_uses_actual_early_close(close_hour):
    # Black Friday 2025 closes 13:00 New York, i.e. 18:00 UTC.
    clock = datetime(2025, 11, 28, close_hour, tzinfo=timezone.utc)
    bar = _daily_session("2025-11-28")
    parsed = parse_penny_daily_aggregates(_daily_payload(bar), as_of=clock)
    assert bool(parsed) is (close_hour == 18)


@pytest.mark.parametrize("session,utc_close", [
    ("2026-03-06", 21), ("2026-03-09", 20),
    ("2026-10-30", 20), ("2026-11-02", 21),
])
def test_penny_daily_parser_respects_dst_closing_boundary(session, utc_close):
    bar = _daily_session(session)
    clock = datetime.fromisoformat(session).replace(hour=utc_close, tzinfo=timezone.utc)
    payload = _daily_payload(bar)
    assert parse_penny_daily_aggregates(payload, as_of=clock - timedelta(seconds=1)) == []
    assert parse_penny_daily_aggregates(payload, as_of=clock)[0]["timestamp"] == bar["t"]


def test_penny_daily_parser_excludes_all_future_and_open_prices_before_ohlcv_validation():
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    closed = _daily_session("2026-10-01")
    open_bar, future = _daily_session("2026-10-02"), _daily_session("2026-10-05")
    del open_bar["h"]
    del future["c"]
    parsed = parse_penny_daily_aggregates(_daily_payload(closed, open_bar, future), as_of=clock)
    assert len(parsed) == 1 and parsed[0]["close"] == closed["c"]


@pytest.mark.parametrize("field,bad", [
    ("o", None), ("o", True), ("h", float("nan")), ("l", 0),
    ("c", -1), ("c", "2.05"), ("h", 1.8), ("v", -1), ("v", False),
    ("t", 1790800000), ("t", None), ("t", True), ("t", float("inf")),
])
def test_penny_daily_parser_rejects_bad_closed_record_instead_of_shortening_history(field, bad):
    good = _daily_session("2026-09-30")
    broken = dict(_daily_session("2026-10-01"), **{field: bad})
    with pytest.raises(ValueError, match="penny_daily_"):
        parse_penny_daily_aggregates(_daily_payload(good, broken),
            as_of=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))


def test_penny_daily_parser_rejects_conflicting_duplicate_and_reversed_timestamps():
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    one, two = _daily_session("2026-09-30"), _daily_session("2026-10-01")
    assert len(parse_penny_daily_aggregates(_daily_payload(one, one, two), as_of=clock)) == 2
    with pytest.raises(ValueError, match="conflicting_timestamp"):
        parse_penny_daily_aggregates(_daily_payload(one, dict(one, h=2.3), two), as_of=clock)
    with pytest.raises(ValueError, match="nonascending_timestamp"):
        parse_penny_daily_aggregates(_daily_payload(two, one), as_of=clock)


@pytest.mark.parametrize("payload", [
    {"status": "ERROR", "results": []}, {"status": "OK"},
    {"status": "OK", "results": None},
    {"status": "OK", "results": [], "adjusted": False},
    {"status": "OK", "resultsCount": 0, "queryCount": 2},
    {"status": "OK", "results": [], "next_url": "https://example.invalid/page"},
    {"status": "OK", "results": [], "resultsCount": True},
])
def test_penny_daily_parser_rejects_unknown_or_incomplete_envelope(payload):
    with pytest.raises(ValueError, match="penny_daily_"):
        parse_penny_daily_aggregates(payload, as_of=datetime.now(timezone.utc))


def test_penny_daily_parser_accepts_known_empty_and_does_not_invent_adjustment_flag():
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    assert parse_penny_daily_aggregates({"status": "OK", "resultsCount": 0}, as_of=clock) == []
    assert parse_penny_daily_aggregates({"status": "DELAYED", "results": []}, as_of=clock) == []
    # API caller asks adjusted=true; optional response metadata may be absent.
    payload = _daily_payload(_daily_session("2026-10-01"))
    del payload["adjusted"]
    assert len(parse_penny_daily_aggregates(payload, as_of=clock)) == 1
    with pytest.raises(ValueError, match="invalid_clock"):
        parse_penny_daily_aggregates(payload, as_of=clock.replace(tzinfo=None))


@pytest.mark.parametrize("title,risk", [
    ("Acme clinical hold issued after safety concerns", "regulatory_risk"),
    ("Acme announces public offering and dilution", "dilution_risk"),
    ("No safety concerns in phase one, but clinical hold issued for phase two", "regulatory_risk"),
])
def test_biotech_real_risk_still_blocks(title, risk):
    result = _edge({"title": title, "description": ""})
    assert result[risk] > 0 and result["trade_mode"] == "AVOID_NEWS_RISK", result


def test_bi_old_rvol_and_biotech_old_news_caches_need_fresh_evidence():
    import api
    from modules.biotech_news_contract import biotech_news_contract_valid
    from modules.patterns import BI_STOCK_CONTRACT_VERSION
    result = _result(17)
    row = scanners._bi_analysis_contract_payload(result)
    assert row is not None and BI_STOCK_CONTRACT_VERSION == "stock-bi-20-v8"
    # Keep the historical version intact: reject it, never relabel it current.
    row["BI_IndicatorContractVersion"] = "stock-bi-20-v6"
    assert api._bi_row_meets_signal_contract(row) is False
    assert biotech_news_contract_valid({"News_Contract_Version": "biotech-news-v2"}) is False
    assert biotech_news_contract_valid({"News_Contract_Version": "biotech-news-v3"}) is True


@pytest.mark.parametrize("title,flags,expected", [
    ("Acme confirms well tolerated therapy with no safety concerns", [], " Positiv"),
    ("Acme confirms its trial is not on clinical hold", [], "—"),
    ("Acme receives complete response letter from FDA",
     [{"flag": "complete response letter"}], " Negativ"),
])
def test_biotech_producer_event_result_agrees_with_canonical_news_risk(monkeypatch, title, flags, expected):
    # Isolate the genuine producer's Event_Result, not a duplicated test model.
    # A fixed high score keeps even adverse-news watch candidates visible here;
    # it is not a mail/entry approval or a relaxation of production thresholds.
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: [
        {"ticker": "TEST", "name": "Acme Biotechnology"}])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    monkeypatch.setattr(scanners, "_scan_biotech_news", lambda *a, **kw: {
        "catalyst_score": 30, "catalysts": [], "best_catalyst": None,
        "had_catalyst_keywords": True, "negative_flags": flags,
        "news": [{"title": title, "description": "", "sentiment": "neutral"}]})
    monkeypatch.setattr(scanners, "get_ticker_details", lambda *a: {
        "sic_code": "2836", "market_cap_millions": 3000, "shares_millions": 30})
    monkeypatch.setattr(scanners, "_get_bpiq_catalysts", lambda *a: {"bpiq_available": False})
    monkeypatch.setattr(scanners, "_biotech_technical_score", lambda *a, **kw: {
        "technical_score": 20, "details": {"price": 20., "chart_health": 10}})
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_score", lambda **kw: 85)
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_edge", lambda **kw: {})
    saved = []
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda rows, **kw: saved.append((rows, kw)))
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *a, **kw: None)
    scanners._biotech_background_scan("offline")
    finals = [rows for rows, meta in saved if not meta.get("partial")]
    assert len(finals) == 1 and finals[0]
    assert finals[0][0]["Event_Result"] == expected


def _biotech_technical_bars():
    bars = [{"open": 20.0, "high": 20.2, "low": 19.6, "close": 20.0,
             "volume": 100000} for _ in range(60)]
    bars[-1].update(close=19.8, volume=299600)
    return bars


def test_biotech_raw_rvol_does_not_falsely_mark_distribution():
    tech = scanners._compute_biotech_technical_from_bars(_biotech_technical_bars())
    result = scanners._calculate_biotech_catalyst_edge(
        {"pipeline_score": 0, "readout_score": 0, "catalyst_readouts": []},
        {"catalyst_score": 0, "negative_flags": [], "news": []}, tech,
        {"market_cap_millions": 3000, "shares_millions": 30})
    assert tech["details"]["RVOL"] == pytest.approx(2.996)
    assert "distribution_volume" not in result["risk_flags"]


@pytest.mark.parametrize("field,value", [("volume", None), ("volume", False), ("open", True)])
def test_biotech_pure_technical_rejects_unknown_or_boolean_ohlcv(field, value):
    bars = _biotech_technical_bars()
    bars[3][field] = value
    result = scanners._compute_biotech_technical_from_bars(bars)
    assert result.get("data_status") == "invalid_ohlcv"
    assert result["details"] == {} and result["technical_score"] == 0


@pytest.mark.parametrize("text,expected", [
    ("FDA approves Acme drug", "—"),  # Only established dictionary rules, no invented interpretation.
    ("FDA approved Acme drug", " Positiv"),
    ("Acme announces favorable safety and complete response", " Positiv"),
    ("Acme reports clinical hold", " Negativ"),
    ("Acme reports positive results. A second trial received a complete response letter", " Gemischt"),
    ("Acme topline results expected next quarter", " Ausstehend"),
    ("Acme has not yet reported positive results", "—"),
])
def test_biotech_shared_event_result_positive_negative_mixed_forward_controls(text, expected):
    news = {"news": [{"title": text}], "negative_flags": [],
            "forward_catalyst": "expected" in text}
    assert scanners._biotech_event_result(news) == expected


def test_biotech_position_at_raw_20_percent_boundary_keeps_its_chart_health():
    bars = [{"open": 12.004, "high": 20.0, "low": 10.0, "close": 12.004,
             "volume": 100000} for _ in range(60)]
    tech = scanners._compute_biotech_technical_from_bars(bars)
    assert tech["details"]["pos_90d"] == pytest.approx(20.04)
    assert tech["details"]["chart_health"] == 6  # Drawdown -4, no false <=20 penalty.


def test_biotech_unconsumed_partial_day_cannot_abort_completed_technical_analysis(monkeypatch):
    clock = datetime(2026, 10, 2, 14, tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock.astimezone(tz) if tz is not None else clock.replace(tzinfo=None)
    monkeypatch.setattr(scanners, "datetime", Clock)
    day = datetime(2026, 9, 1)
    prior = [_daily_session((day + timedelta(days=i)).date().isoformat())
             for i in range(31) if (day + timedelta(days=i)).weekday() < 5]
    partial = _daily_session("2026-10-02")
    del partial["v"]
    # Unknown volume in today's open candle must not defeat yesterday's real
    # daily evidence. The bar is excluded before mandatory OHLCV validation.
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Reply(
        payload=_daily_payload(*prior, partial)))
    result = scanners._biotech_technical_score("offline", "TEST")
    assert result.get("data_status") != "invalid_ohlcv"
    assert result["details"]["price"] == prior[-1]["c"]


@pytest.mark.parametrize("mode,clock,expected_session", [
    ("starter_swing", "2026-10-02T20:14:59+00:00", "2026-10-01"),
    ("starter_swing", "2026-10-02T20:15:00+00:00", "2026-10-02"),
    ("live", "2026-10-02T20:00:00+00:00", "2026-10-02"),
])
def test_biotech_technical_session_cutoff_matches_plan_delay(monkeypatch, mode, clock, expected_session):
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", mode)
    prior = [_daily_session((datetime(2026, 9, 1) + timedelta(days=i)).date().isoformat())
             for i in range(32) if (datetime(2026, 9, 1) + timedelta(days=i)).weekday() < 5]
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Reply(payload=_daily_payload(*prior)))
    result = scanners._biotech_technical_score("offline", "TEST", as_of=datetime.fromisoformat(clock))
    assert result["details"]["analysis_session"] == expected_session
    assert result["details"]["analysis_as_of"] == clock


def test_biotech_closed_invalid_day_still_rejects_the_whole_series(monkeypatch):
    prior = [_daily_session((datetime(2026, 9, 1) + timedelta(days=i)).date().isoformat())
             for i in range(31) if (datetime(2026, 9, 1) + timedelta(days=i)).weekday() < 5]
    del prior[-1]["v"]
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: Reply(payload=_daily_payload(*prior)))
    with pytest.raises(scanners.ScannerDataError, match="scan_data_invalid"):
        scanners._biotech_technical_score("offline", "TEST",
            as_of=datetime(2026, 10, 2, 14, tzinfo=timezone.utc))


def test_biotech_full_scan_passes_one_fixed_clock_to_every_news_and_technical_call(monkeypatch):
    monkeypatch.setattr(scanners, "_biotech_clear_stop", lambda: None)
    monkeypatch.setattr(scanners, "_biotech_should_stop", lambda: False)
    monkeypatch.setattr(scanners, "_biotech_universe_cache_load", lambda **kw: [
        {"ticker": "ONE", "name": "One Biotechnology"},
        {"ticker": "TWO", "name": "Two Biotechnology"}])
    monkeypatch.setattr(scanners, "get_premium_catalyst_tickers", lambda **kw: set())
    clocks = []
    def news(*a, **kw):
        clocks.append(kw.get("as_of"))
        return {"catalyst_score": 30, "catalysts": [], "negative_flags": [], "news": []}
    def technical(*a, **kw):
        clocks.append(kw.get("as_of"))
        return {"technical_score": 20, "details": {"price": 20., "chart_health": 10}}
    monkeypatch.setattr(scanners, "_scan_biotech_news", news)
    monkeypatch.setattr(scanners, "_biotech_technical_score", technical)
    monkeypatch.setattr(scanners, "get_ticker_details", lambda *a: {"sic_code": "2836"})
    monkeypatch.setattr(scanners, "_get_bpiq_catalysts", lambda *a: {"bpiq_available": False})
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_score", lambda **kw: 85)
    monkeypatch.setattr(scanners, "_calculate_biotech_catalyst_edge", lambda **kw: {})
    monkeypatch.setattr(scanners, "_biotech_cache_save", lambda *a, **kw: None)
    monkeypatch.setattr(scanners, "_biotech_progress_write", lambda *a, **kw: None)
    scanners._biotech_background_scan("offline")
    assert len(clocks) == 4 and clocks[0] is not None and clocks[0].tzinfo is not None
    assert all(clock is clocks[0] for clock in clocks)


@pytest.mark.parametrize("title", [
    "Acme FDA approval was denied",
    "Acme primary endpoint was not met",
])
def test_biotech_edge_honors_canonical_failure_pattern_flags(monkeypatch, title):
    from test_biotech_deep_fixes import _classify
    news = _classify(monkeypatch, title)
    assert news["negative_flags"] and news["catalyst_score"] == 0
    edge = scanners._calculate_biotech_catalyst_edge(
        {"catalyst_readouts": []}, news,
        {"technical_score": 10, "details": {"price": 20, "chart_health": 8}},
        {"market_cap_millions": 3000, "shares_millions": 30})
    assert edge["regulatory_risk"] >= 30 and edge["trade_mode"] == "AVOID_NEWS_RISK"
