"""Acceptance regressions for shared-module scanner/delivery audit findings."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier

import pytest


def test_outbox_concurrent_enqueue_serializes_lookup_and_insert(tmp_path, monkeypatch):
    from modules import mail_outbox as outbox
    db = str(tmp_path / "outbox.sqlite")
    assert outbox.init_db(db)
    monkeypatch.setattr(outbox, "outbox_enabled", lambda: True)
    real_connect = outbox._connect
    simultaneous = Barrier(2)
    begin_calls = []

    class Connection:
        def __init__(self, conn):
            self.conn = conn
        def __enter__(self):
            self.conn.__enter__()
            return self
        def __exit__(self, *args):
            result = self.conn.__exit__(*args)
            self.conn.close()
            return result
        def __getattr__(self, name):
            return getattr(self.conn, name)
        def execute(self, sql, *args):
            if sql == "BEGIN IMMEDIATE":
                begin_calls.append(sql)
                simultaneous.wait(timeout=5)
            return self.conn.execute(sql, *args)

    monkeypatch.setattr(outbox, "_connect", lambda db_path=None: Connection(real_connect(db_path)))
    def enqueue():
        return outbox.enqueue("same", "body", ["audit@example.invalid"], now=1000, db_path=db)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(enqueue), pool.submit(enqueue)]
        ids = [future.result(timeout=10) for future in futures]
    monkeypatch.setattr(outbox, "_connect", real_connect)
    assert len(begin_calls) == 2
    assert ids[0] is not None and ids[0] == ids[1]
    sent = []
    result = outbox.process_outbox(lambda row: sent.append(row), now=1001, db_path=db)
    assert result["sent"] == 1 and len(sent) == 1


def test_outbox_rechecks_each_ttl_and_records_actual_receipt_time(tmp_path, monkeypatch):
    from modules import mail_outbox as outbox
    db = str(tmp_path / "outbox.sqlite")
    for subject in ("first", "expires during first send"):
        assert outbox.enqueue(subject, "body", ["audit@example.invalid"],
                              mail_class="signal_update", now=1000, db_path=db)
    clock = [1895.0]
    monkeypatch.setattr(outbox.time, "time", lambda: clock[0])
    sent = []
    def deliver(row):
        sent.append(row["subject"])
        clock[0] += 20
    result = outbox.process_outbox(deliver, db_path=db)
    assert sent == ["first"]
    assert result["sent"] == 1 and result["expired"] == 1
    with outbox._connect(db) as conn:
        rows = conn.execute("SELECT status,sent_at FROM mail_outbox ORDER BY id").fetchall()
    assert tuple(rows[0]) == ("sent", 1915.0)
    assert tuple(rows[1]) == ("expired", None)


def test_outbox_expired_claim_cannot_enter_smtp_boundary(tmp_path):
    from modules import mail_outbox as outbox
    db = str(tmp_path / "outbox.sqlite")
    item = outbox.enqueue("update", "body", ["audit@example.invalid"],
                          mail_class="signal_update", now=1000, db_path=db)
    assert outbox._claim_due_items(now=1899, db_path=db)[0]
    assert outbox.mark_delivering(item, now=1900, db_path=db) is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("exact_rr,acceptable", [(1.495,False),(1.4951,False),(1.4999,False),(1.5,True),(1.5001,True)])
def test_primary_target_gate_uses_exact_geometry(direction, exact_rr, acceptable):
    from modules.trade_levels import normalize_alert_trade_levels, trade_plan_quality
    sign = 1 if direction == "LONG" else -1
    levels = normalize_alert_trade_levels(dict(entry=100, stop=100-sign*10,
        tp1=100+sign*10*exact_rr, tp2=100+sign*25, direction=direction), allow_estimated=False)
    quality = trade_plan_quality(levels)
    assert quality["tp1_ok"] is acceptable
    assert ("tp1_rr_below_primary_threshold" in quality["issues"]) is not acceptable
    assert quality["rr_tp1"] == pytest.approx(exact_rr)


@pytest.mark.parametrize("direction,entry,stop,tp1,tp2", [
    ("SHORT",12.,12.8,10.8,10.),("LONG",20.,19.2,21.2,22.),
])
def test_nominal_decimal_price_rr_boundary_accepts_only_machine_error(direction,entry,stop,tp1,tp2):
    from modules.trade_levels import normalize_alert_trade_levels, trade_plan_quality
    levels = normalize_alert_trade_levels(dict(direction=direction,entry=entry,stop=stop,
        tp1=tp1,tp2=tp2),allow_estimated=False)
    quality = trade_plan_quality(levels)
    assert quality["rr_tp1"] < 1.5  # The original floating-point counterexample.
    assert quality["tp1_ok"] is True
    assert quality["issues"] == []


@pytest.mark.parametrize("gap_error,accepted", [(1e-15,True),(1e-5,False)])
def test_minimum_target_gap_uses_only_machine_precision_tolerance(gap_error,accepted):
    from modules.trade_levels import trade_plan_quality
    quality = trade_plan_quality(dict(entry=10.,risk=.8,reward1=1.2,
        reward2=1.6-gap_error))
    assert ("targets_too_close" not in quality["issues"]) is accepted


def test_tiny_price_target_gap_is_not_relaxed_by_absolute_currency_tolerance():
    from modules.trade_levels import trade_plan_quality
    quality = trade_plan_quality(dict(entry=1e-10,risk=1e-14,
        reward1=2e-14,reward2=3e-14))
    assert "targets_too_close" in quality["issues"]


@pytest.mark.parametrize("session,close_hour", [("2026-11-27",18),("2026-07-02",20),
    ("2026-09-23",20),("2026-12-23",21)])
def test_vrvp_date_adapter_shares_exchange_close_and_completed_boundary(session, close_hour):
    from modules.vrvp_levels import _date_temporal_adapter, normalize_ohlcv_bars
    raw = dict(date=session, open=100, high=105, low=99, close=104, volume=10000)
    adapted = _date_temporal_adapter(raw, timeframe="1D", date_session_context="us_equity_regular")
    close = datetime.fromisoformat(session).replace(hour=close_hour, tzinfo=timezone.utc)
    assert adapted["close_time"] == close
    kwargs = dict(timeframe="1D", date_session_context="us_equity_regular")
    assert normalize_ohlcv_bars([raw], as_of=close-timedelta(seconds=1), **kwargs) == []
    assert len(normalize_ohlcv_bars([raw], as_of=close, **kwargs)) == 1


@pytest.mark.parametrize("session", ["2026-11-26", "2026-11-28"])
def test_vrvp_does_not_invent_holiday_or_weekend_session(session):
    from modules.vrvp_levels import _date_temporal_adapter
    assert _date_temporal_adapter(dict(date=session), timeframe="1D",
                                  date_session_context="us_equity_regular") is None


@pytest.mark.parametrize("reward1,reward2", [(1e308,1.5e308),(1.5,1e308)])
def test_exact_target_ratio_overflow_fails_closed(reward1, reward2):
    from modules.trade_levels import trade_plan_quality
    result = trade_plan_quality(dict(entry=1., risk=1e-308,
        reward1=reward1, reward2=reward2, rr_tp1=2., rr_tp2=3., rr=2.5))
    assert result["tp1_ok"] is False
    assert result["effective_rr"] is None
    assert result["issues"] == ["missing_target_rr"]


def test_finite_large_target_ratios_do_not_overflow_on_averaging():
    import math
    from modules.trade_levels import trade_plan_quality
    result = trade_plan_quality(dict(entry=1.,risk=1.,reward1=1e308,reward2=1.5e308))
    assert math.isfinite(result["effective_rr"])
    assert result["tp1_ok"] is True


def _bars(closes, volumes=None):
    return [dict(open=value, high=value+.1, low=value-.1, close=value,
                 volume=volumes[i] if volumes else 1000.) for i,value in enumerate(closes)]


@pytest.mark.parametrize("direction", [1,-1])
def test_real_flag_has_impulse_shallow_retracement_and_contracting_volume(direction):
    from modules.analysis import analyze_multi_day_pattern
    prices = [100.]*12 + [100.,103.,106.,110.] + [109.,108.5,108.8,109.]
    prices = [100+direction*(value-100) for value in prices]
    bars = _bars(prices, [1000.]*12+[3000.]*4+[1200.]*4)
    name = "bull_flag" if direction == 1 else "bear_flag"
    valid, score, details = analyze_multi_day_pattern(bars, name)
    assert valid and score >= 45
    assert any("<50%" in detail for detail in details)
    assert not any("Retest" in detail for detail in details)
    for volume in (4000.,0.):
        bad = [dict(bar, volume=volume) if i >= 16 else dict(bar) for i,bar in enumerate(bars)]
        assert analyze_multi_day_pattern(bad, name)[0] is False
    deep = _bars(prices[:16]+[100+direction*3]*4, [1000.]*12+[3000.]*4+[1200.]*4)
    assert analyze_multi_day_pattern(deep, name)[0] is False


@pytest.mark.parametrize("direction", [1,-1])
def test_eighty_three_percent_failed_flag_is_rejected(direction):
    from modules.analysis import analyze_multi_day_pattern
    prices = [100.,100+10*direction,100+20*direction,100+30*direction]+[100+5*direction]*16
    bars = _bars(prices, [1000.]*18+[2000.]*2)
    assert analyze_multi_day_pattern(bars, "bull_flag" if direction == 1 else "bear_flag")[0] is False


def test_compression_needs_closed_break_not_wick_and_accepts_no_retest():
    from modules.analysis import analyze_multi_day_pattern
    bars = [dict(open=100.,high=100.8,low=99.5,close=100.,volume=1000.) for _ in range(15)]
    bars[-1].update(high=102.5,close=100.1,volume=3000.)
    assert analyze_multi_day_pattern(bars, "consolidation_breakout")[0] is False
    bars[-1].update(close=102.)
    valid,score,details = analyze_multi_day_pattern(bars,"consolidation_breakout")
    assert valid and score >= 55
    assert any("Retest optional" in value for value in details)
    bars[-1]["volume"] = 1000.
    assert analyze_multi_day_pattern(bars,"consolidation_breakout")[0] is False


def test_open_compression_breakout_does_not_confirm_and_future_bar_is_ignored():
    from modules.analysis import analyze_multi_day_pattern
    start = datetime(2026,1,1,tzinfo=timezone.utc)
    bars = [dict(open_time=start+timedelta(days=i), close_time=start+timedelta(days=i+1),
                 open=100., high=100.8, low=99.5, close=100., volume=1000.) for i in range(15)]
    bars[-1].update(high=102.5,close=102.,volume=3000.)
    cutoff = start+timedelta(days=15)
    assert analyze_multi_day_pattern(bars,"consolidation_breakout",as_of=cutoff-timedelta(seconds=1),timeframe="1D")[0] is False
    baseline = analyze_multi_day_pattern(bars,"consolidation_breakout",as_of=cutoff,timeframe="1D")
    assert baseline[0] is True
    future = dict(bars[-1], open_time=cutoff,close_time=cutoff+timedelta(days=1), high=1000.,close=999.)
    assert analyze_multi_day_pattern(bars+[future],"consolidation_breakout",as_of=cutoff,timeframe="1D") == baseline


def _piecewise(points, count=60):
    rows = []
    day = datetime(2025,1,2,tzinfo=timezone.utc)
    for i in range(count):
        while day.weekday() >= 5:
            day += timedelta(days=1)
        left,right = next((a,b) for a,b in zip(points,points[1:]) if a[0] <= i <= b[0])
        value = left[1]+(right[1]-left[1])*(i-left[0])/(right[0]-left[0])
        rows.append(dict(time=int(day.timestamp()),date=day.date().isoformat(),open=value,
                         high=value+.05,low=value-.05,close=value,volume=1000.))
        day += timedelta(days=1)
    return rows


@pytest.mark.parametrize("direction", [1,-1])
def test_harmonic_accepts_real_canonical_history_and_preserves_chart_points(direction):
    import api
    from modules.patterns import find_harmonic_for_chart
    bars = _piecewise([(0,111),(165,110),(170,100),(180,140),(190,115.28),(200,130.5),(210,108.56),(219,109.5)],220)
    if direction == -1:
        bars = [dict(bar,open=250-bar["open"],high=250-bar["low"],low=250-bar["high"],close=250-bar["close"]) for bar in bars]
    canonical = api._stock_completed_pattern_history(bars,as_of=datetime(2026,9,23,tzinfo=timezone.utc))
    assert len(canonical) == 220 and "time" not in canonical[-1]
    chart, scanner = find_harmonic_for_chart(bars), find_harmonic_for_chart(canonical)
    assert chart and scanner
    assert any(p["pattern"] == "Gartley" for p in scanner)
    assert [(p["pattern"],p["trade"],p["ratios"]) for p in scanner] == [(p["pattern"],p["trade"],p["ratios"]) for p in chart]
    assert all(len(p["points"]) == 5 and all(isinstance(point["time"],int) for point in p["points"]) for p in scanner)
    candidate = dict(_daily_bars=canonical,price=bars[-1]["close"],Dollar_Volume=5_000_000,base_score=90)
    strategy = dict(harmonic_direction="LONG" if direction == 1 else "SHORT",min_dollar_volume=1_000_000)
    assert api._apply_harmonic_strategy_filter(candidate,strategy) is not None


@pytest.mark.parametrize("direction", [1,-1])
def test_real_narrowing_wolfe_survives_chart_adapter(direction):
    from modules.patterns import detect_wolfe_waves,detect_chart_patterns
    bars = _piecewise([(0,110),(5,100),(15,130),(25,95),(35,120),(45,80),(59,85)])
    if direction == -1:
        bars = [dict(bar,open=250-bar["open"],high=250-bar["low"],low=250-bar["high"],close=250-bar["close"]) for bar in bars]
    wanted = "bullish" if direction == 1 else "bearish"
    waves = detect_wolfe_waves(bars,lookback=60,min_wave_bars=3,max_wave_bars=25)
    assert any(w["direction"] == wanted for w in waves)
    chart = detect_chart_patterns(bars,lookback=60)
    matches = [p for p in chart if "Wolfe" in p["pattern"] and p["type"] == wanted]
    assert matches and len(matches[0]["draw_points"]) == 5
    assert all(0 <= point["index"] < len(bars) for point in matches[0]["draw_points"])


def test_widening_bullish_channel_is_not_a_wolfe_wave():
    from modules.patterns import detect_wolfe_waves
    bars = _piecewise([(0,110),(5,100),(15,130),(25,90),(35,125),(45,75),(59,80)])
    waves = detect_wolfe_waves(bars,lookback=60,min_wave_bars=3,max_wave_bars=25)
    assert not any(w["direction"] == "bullish" for w in waves)


def test_actual_chart_route_keeps_wolfe_points(monkeypatch):
    import api
    bars = _piecewise([(0,110),(5,100),(15,130),(25,95),(35,120),(45,80),(59,85)])
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *args,**kwargs: bars)
    result = api.get_chart_data(ticker="AUDT",timeframe="1D",overlays="patterns",direction="LONG")
    chart_patterns = result.get("patterns",{}).get("chart_patterns",[])
    matches = [p for p in chart_patterns if "Wolfe" in p.get("pattern","")]
    assert matches, result.get("patterns")
    assert len(matches[0]["draw_points"]) == 5
    assert all(point.get("time") is not None for point in matches[0]["draw_points"])


@pytest.mark.parametrize("direction",[1,-1])
def test_real_flag_survives_stock_post_filter(direction):
    import api
    prices = [100.]*12+[100.,103.,106.,110.]+[109.,108.5,108.8,109.]
    prices = [100+direction*(value-100) for value in prices]
    bars = _bars(prices,[1000.]*12+[3000.]*4+[1200.]*4)
    candidate = dict(ticker="FLAG",_daily_bars=bars,price=prices[-1],Dollar_Volume=5_000_000,
                     base_score=80,Change_Pct=0,RVOL=1.5)
    result = api._apply_pattern_strategy_filter(candidate,api.STRATEGIES["Bull Flag" if direction == 1 else "Bear Flag"])
    assert result is not None and result["pattern_score"] >= 45


def test_closed_compression_survives_stock_post_filter_without_retest():
    import api
    bars = [dict(open=100.,high=100.8,low=99.5,close=100.,volume=1000.) for _ in range(15)]
    bars[-1].update(high=102.5,close=102.,volume=3000.)
    candidate = dict(ticker="COMP",_daily_bars=bars,price=102.,Dollar_Volume=5_000_000,
                     base_score=80,Change_Pct=2.,RVOL=3.)
    result = api._apply_pattern_strategy_filter(candidate,api.STRATEGIES["Compression Breakout"])
    assert result is not None and result["pattern_score"] >= 55


@pytest.mark.parametrize("ratio,label",[(.236,"23.6%"),(.382,"38.2%"),(.5,"50%"),(.618,"61.8%"),(.786,"78.6%"),(1.618,"161.8%")])
def test_fibonacci_display_keeps_actual_percentage(ratio,label):
    from modules.fibonacci_levels import _ratio_label
    assert _ratio_label(ratio) == label
