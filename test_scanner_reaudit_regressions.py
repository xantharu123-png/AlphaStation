"""Regression counterexamples from the b6be1f2 re-audit; offline I/O only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
import api
from modules.patterns import detect_order_blocks, detect_volume_imbalances


NOW = datetime(2026, 9, 30, 13, 50, tzinfo=timezone.utc)
REAL_FINAL_VALIDATION = api._revalidate_stock_strategy_mail_candidate


def freeze(monkeypatch, now=NOW):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())


def candle(o=100, h=100.4, l=99.6, c=100, v=1000):
    return dict(open=o, high=h, low=l, close=c, volume=v)


def mirror(bars):
    return [dict(b, open=200-b["open"], high=200-b["low"],
                 low=200-b["high"], close=200-b["close"]) for b in bars]


def gap_history():
    return ([candle() for _ in range(25)]
            + [candle(100, 101.4, 99.9, 101.3, 3000),
               candle(101.5, 101.8, 101.4, 101.7)])


def ob_history():
    return ([candle() for _ in range(27)]
            + [candle(100.2, 100.4, 99.6, 99.8),
               candle(99.9, 102.2, 99.8, 102, 3000),
               candle(102, 102.4, 101.8, 102.2)])


@pytest.mark.parametrize("bear", [False, True])
def test_fvg_established_zone_survives_later_noncontact_volatility(bear):
    bars = gap_history()
    future = candle(102.1, 180, 102, 103)
    if bear:
        bars, future = mirror(bars), mirror([future])[0]
    before = detect_volume_imbalances(bars)["zones"]
    zone = next(z for z in before if z["type"] == "FVG" and z["bar_idx"] == 26)
    assert zone["filled"] is False
    after = detect_volume_imbalances(bars + [future])["zones"]
    assert any(z["bar_idx"] == zone["bar_idx"] and z["type"] == "FVG" for z in after), (zone, after)


@pytest.mark.parametrize("bear", [False, True])
def test_ob_established_zone_survives_later_noncontact_volatility(bear):
    bars = ob_history()
    future = candle(102.3, 180, 102, 103)
    key = "bearish_obs" if bear else "bullish_obs"
    if bear:
        bars, future = mirror(bars), mirror([future])[0]
    before = detect_order_blocks(bars)[key]
    zone = next(z for z in before if z["idx"] == 27)
    after = detect_order_blocks(bars + [future])[key]
    assert any(z["idx"] == zone["idx"] for z in after), (zone, after)


@pytest.mark.parametrize("bear", [False, True])
def test_fvg_jump_over_zone_is_not_proven_traded_fill(bear):
    bars = gap_history()
    jump = candle(99, 99.2, 98.5, 98.8)
    if bear:
        bars, jump = mirror(bars), mirror([jump])[0]
    zone = next(z for z in detect_volume_imbalances(bars)["zones"] if z["type"] == "FVG" and z["bar_idx"] == 26)
    assert jump["high"] < zone["zone_low"] if not bear else jump["low"] > zone["zone_high"]
    after = next(z for z in detect_volume_imbalances(bars + [jump])["zones"] if z["bar_idx"] == 26 and z["type"] == "FVG")
    assert after["filled"] is False, after


def test_chart_open_displacement_candle_must_not_confirm_orderblock(monkeypatch):
    freeze(monkeypatch)
    bars = ob_history()
    for i, bar in enumerate(bars):
        bar["time"] = int((NOW - timedelta(hours=4*(len(bars)-1-i), minutes=30)).timestamp())
    # The 4H candle closing the two-bar displacement has only run for 30 min.
    bars[-2] = dict(bars[-2], open=99.9, high=100.6, low=99.8, close=100.5, volume=1000)
    assert detect_order_blocks(bars)["bullish_obs"]
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars)
    calculated = []
    real_patterns = api.detect_chart_patterns
    def capture_patterns(data, *args, **kwargs):
        detected = real_patterns(data, *args, **kwargs)
        calculated.append((deepcopy(data), deepcopy(detected)))
        return detected
    monkeypatch.setattr(api, "detect_chart_patterns", capture_patterns)
    result = api.get_chart_data(ticker="AUDT", timeframe="4H", overlays="patterns", direction="LONG")
    patterns = result.get("patterns", {}).get("chart_patterns", [])
    assert result["patterns"]["confirmation_basis"] == "completed_candles"
    assert len(calculated[-1][0]) == len(bars) - 1
    assert not any("Order Block" in str(p.get("pattern")) for p in patterns), patterns
    # Same real data does confirm the block after the last interval closes.
    freeze(monkeypatch, NOW + timedelta(hours=4))
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    closed = api.get_chart_data(ticker="AUDT", timeframe="4H", overlays="patterns", direction="LONG")
    assert len(calculated[-1][0]) == len(bars)
    assert any("Order Block" in str(p.get("pattern")) for p in calculated[-1][1])


class Response:
    def __init__(self, payload=None, status=200):
        self.payload, self.status_code = payload, status
    def json(self):
        return self.payload


def setup_orb(monkeypatch, *, short=True, fault=None, current=None, bars_override=None):
    freeze(monkeypatch)
    start = int(NOW.replace(hour=13, minute=30).timestamp()*1000)
    bars = [{"t": start+i*300000, "o": 99.5, "h": 100, "l": 98.6, "c": 99.6, "v": 100000} for i in range(3)]
    bars.append({"t": start+900000, "o": 99.95, "h": 100.15, "l": 99.9, "c": 100.1, "v": 300000})
    if short:
        bars = [dict(b, o=200-b["o"], h=200-b["l"], l=200-b["h"], c=200-b["c"]) for b in bars]
    if bars_override:
        bars = bars_override(bars)
    snap_price = (99.95 if short else 100.05) if current is None else current
    snapshot = {"tickers": [{"ticker": "AUDT", "day": {"o": 102 if short else 98, "h": 102, "l": 98, "c": snap_price, "v": 2000000}}]}
    def get(url, **kw):
        if "/aggs/" in url:
            return Response(status=503) if fault == "all_5m_http503" else Response({"results": bars})
        return Response(status=503) if fault == "snapshot_http503" else Response(snapshot)
    monkeypatch.setattr(api, "rate_limited_get", get)
    def grouped(*args):
        if args[-1] == "2026-09-30":
            return {}  # no final grouped daily during the morning
        return {"AUDT": {"c": 101 if short else 99, "h": 103 if short else 101, "l": 99 if short else 97, "v": 1000000}}
    monkeypatch.setattr(api, "fetch_grouped_daily", grouped)
    monkeypatch.setattr(api, "_fetch_orb_atr_pct", lambda *a: (2, "fixture"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **kw: row)
    monkeypatch.setattr(api, "_filter_open_equivalent_trade_rows", lambda scanner, rows: (rows, 0))
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a, **kw: None)
    saved, sends, validates = [], [], []
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **kw: sends.append((a, kw)) or True)
    def validation(row, **kw):
        validates.append((deepcopy(row), kw))
        return {"ok": False, "reason": "audit_stops_before_external_quote"}
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validation)
    return saved, sends, validates


@pytest.mark.parametrize("short", [False, True])
def test_orb_positive_completed_breakout_has_consistent_direction(monkeypatch, short):
    saved, sends, validations = setup_orb(monkeypatch, short=short)
    api._orb_scanner_wrapper()
    assert len(saved[0]["actionable_breakouts"]) == 1, saved
    row = saved[0]["actionable_breakouts"][0]
    assert row["direction"] == ("SHORT" if short else "LONG")
    assert api._orb_target_plan_metrics(row)["valid"] is True
    assert row["trade_decision"] == "TRADEABLE"
    assert api._classify_alert_candidate("orb", row, NOW.timestamp())["alertable_now"] is True
    assert len(validations) == 1
    assert not sends  # audit never permits a real SMTP boundary


@pytest.mark.parametrize("fault", ["all_5m_http503", "snapshot_http503"])
def test_orb_data_outage_must_not_publish_successful_empty_cache(monkeypatch, fault):
    saved, sends, validations = setup_orb(monkeypatch, fault=fault)
    try:
        api._orb_scanner_wrapper()
    except api.ScannerDataError:
        pass
    assert saved == [], saved
    assert sends == []


@pytest.mark.parametrize("short", [False, True])
def test_orb_bad_bar_must_not_erase_current_return_into_range(monkeypatch, short):
    freeze(monkeypatch, NOW + timedelta(minutes=5))
    def add_invalid(bars):
        bad = dict(bars[-1], t=bars[-1]["t"]+300000, c=100.5 if short else 99.5, v=float("nan"))
        bad.update(h=101, l=99, o=100)
        return bars + [bad]
    saved, sends, validations = setup_orb(monkeypatch, short=short, bars_override=add_invalid)
    freeze(monkeypatch, NOW + timedelta(minutes=5))
    api._orb_scanner_wrapper()
    assert saved[0]["actionable_breakouts"] == [], saved


def configure_final_orb(monkeypatch, short, fault=None):
    saved, sends, _ = setup_orb(monkeypatch, short=short)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **kw: 0)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **kw: True)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", lambda *a, **kw: None)
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *a: "US_REGULAR")
    now = NOW.timestamp()
    price = 99.95 if short else 100.05
    if fault == "quote_back_inside_or":
        price = 100.1 if short else 99.9
    elif fault == "quote_at_or_boundary":
        price = 100.01 if short else 99.99  # executable bid/ask exactly 100
    quote = dict(ok=True, observed_ts=now, receipt_ts=now,
                 last_trade_ts=now, bid=price-.01, ask=price+.01)
    if fault == "quote_missing":
        quote = dict(ok=False, reason="final_quote_missing_fixture")
    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", lambda *a, **kw: deepcopy(quote))
    def path(*args, **kwargs):
        bar = dict(timestamp=now, open=price, high=price+.02, low=price-.02, close=price)
        if fault == "path_stop_breached":
            bar["high" if short else "low"] = 102 if short else 98
        if fault == "path_tp1_touched":
            bar["low" if short else "high"] = 98 if short else 102
        return dict(ok=True, bars=[bar], coverage_verified=True,
                    coverage_start_timestamp=now, coverage_end_timestamp=now,
                    first_timestamp=now, last_timestamp=now, source="offline_fixture")
    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", path)
    validations = []
    def observe(row, **kw):
        result = REAL_FINAL_VALIDATION(row, **kw)
        validations.append(result)
        return result
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", observe)
    return saved, sends, validations


@pytest.mark.parametrize("short", [False, True])
@pytest.mark.parametrize("fault", [None, "quote_missing", "path_stop_breached", "path_tp1_touched", "quote_back_inside_or", "quote_at_or_boundary"])
def test_orb_native_engine_final_revalidation_to_mail_boundary(monkeypatch, short, fault):
    saved, sends, validations = configure_final_orb(monkeypatch, short, fault)
    api._orb_scanner_wrapper()
    assert validations, saved
    if fault is None:
        assert len(sends) == 1, validations
        delivered = sends[0][1]["tracking_rows"][0]
        assert delivered["direction"] == ("SHORT" if short else "LONG")
        assert delivered["price_mode"] == ("bid" if short else "ask")
        assert delivered["fill_evidence_verified"] is False
        assert delivered["quote_evidence_verified"] is True
    else:
        assert not sends, {"validation": validations, "mock_sends": sends}
