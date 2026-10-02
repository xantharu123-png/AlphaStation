"""Legacy stock labels must not falsely describe unrelated scanners or facts."""
import pytest

import api


@pytest.mark.parametrize("prefix", ["Volume Void Long", "Volume Void Short", "High Volume Churn"])
def test_hidden_volume_strategy_request_never_routes_to_unrelated_volume_spikes(monkeypatch, prefix):
    name = next(n for n in api.STOCK_STRATEGY_HIDDEN if n.startswith(prefix))
    # Admission is intentionally exercised, but its registration must not leak
    # into unrelated tests sharing the imported API singleton.
    monkeypatch.setattr(api, "_scan_status", dict(api._scan_status))
    monkeypatch.setattr(api, "SCAN_CACHE_MAP", dict(api.SCAN_CACHE_MAP))
    monkeypatch.setattr(api, "SCAN_DATA_SOURCES", dict(api.SCAN_DATA_SOURCES))
    invoked = []
    monkeypatch.setattr(api, "_run_scan_safe", lambda key, func: invoked.append(key) or True)
    monkeypatch.setattr(api, "_manual_scan_ack", lambda key, accepted, **kw: {"scan_key": key})
    try:
        response = api.run_scan(api.ScanRequest(strategy=name, market_type="stocks"), api.BackgroundTasks())
    except api.HTTPException as exc:
        assert exc.status_code == 501
        assert invoked == []
        return
    assert response["scan_key"] == api._strategy_scan_status_key(name, "stocks")
    assert invoked == [api._strategy_scan_status_key(name, "stocks")]
    assert api.SCAN_CACHE_MAP[response["scan_key"]] == api._strategy_cache_path(name)
    assert api.SCAN_DATA_SOURCES[response["scan_key"]] == api.SCAN_DATA_SOURCES["stock_strategy"]


@pytest.mark.parametrize("name", ["Insider Buying", "Insider Selling"])
def test_insider_label_requires_real_insider_transaction_evidence(monkeypatch, name):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    original_postfilter = api._apply_special_strategy_post_filter
    _wrapper_fixture(monkeypatch)
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", original_postfilter)
    # The source is a valid, ordinary price snapshot, not a Form-4 transaction.
    # No real I/O, SMTP, worker start or production cache is permitted.
    try:
        rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    except api.HTTPException as exc:
        assert exc.status_code == 501
        return
    except api.ScannerDataError as exc:
        assert exc.code in {"scan_data_unavailable", "scan_data_incomplete"}
        return
    assert rows == []


@pytest.mark.parametrize("mirror", [False, True])
def test_harmonic_known_pattern_cannot_ignore_its_required_terminal_ratio(mirror):
    from modules.patterns import identify_harmonic_pattern
    # Three Gartley ratios fit, but AD/XA is 0.6822, far outside the already
    # declared 0.786 +/-3%. A different valid pattern may still be reported.
    values = [100., 140., 115.28, 124.72304, 112.71101312]
    if mirror:
        values = [250-v for v in values]
    types = ["low", "high", "low", "high", "low"]
    if mirror:
        types = ["high" if t == "low" else "low" for t in types]
    pivots = [{"type": t, "price": p, "index": i*4} for i, (t,p) in enumerate(zip(types, values))]
    prices = [{"close": values[-1], "volume": 1000.} for _ in range(17)]
    patterns = identify_harmonic_pattern(pivots, prices)
    assert not any(p["pattern"] == "Gartley" for p in patterns)


@pytest.mark.parametrize("direction", [1, -1])
def test_harmonic_scanner_cannot_confirm_terminal_edge_before_right_bars_close(direction):
    from test_deep_audit_shared_fixes import _piecewise
    bars = _piecewise([(0,111),(165,110),(170,100),(180,140),(190,115.28),(200,130.5),(219,108.56)], 220)
    if direction == -1:
        bars = [dict(b, open=250-b["open"], high=250-b["low"], low=250-b["high"], close=250-b["close"]) for b in bars]
    candidate = dict(_daily_bars=bars, price=bars[-1]["close"], Dollar_Volume=5_000_000, base_score=90)
    strategy = dict(harmonic_direction="LONG" if direction == 1 else "SHORT", min_dollar_volume=1_000_000)
    assert api._apply_harmonic_strategy_filter(candidate, strategy) is None


@pytest.mark.parametrize("price,rvol,expected", [
    (98., .6, True), (98., 1.5, True), (98., .59999, False),
    (98., 1.50001, False), (98.00001, 1., False), (91.99999, 1., False),
    (9.99999, 1., False),
])
def test_hidden_dip_buy_uses_its_raw_existing_filter_boundaries(monkeypatch, price, rvol, expected):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, _metrics
    _wrapper_fixture(monkeypatch, price=price, metrics=_metrics(rvol20=rvol))
    source = api._fetch_strategy_snapshot_universe("Dip Buy")
    source[0]["prevDay"]["c"] = 100.
    source[0]["day"].update(o=price, h=price+.1, l=price-.1)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *_a: source)
    rows = api._strategy_scan_wrapper("Dip Buy", send_email=False, publish_generic_cache=False)
    assert bool(rows) is expected


def test_hidden_dip_buy_negative_day_does_not_turn_a_buy_setup_into_short(monkeypatch):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, _metrics
    _wrapper_fixture(monkeypatch, price=95., metrics=_metrics(rvol20=1.))
    source = api._fetch_strategy_snapshot_universe("Dip Buy")
    source[0]["prevDay"]["c"] = 100.
    source[0]["day"].update(o=95., h=95.1, l=94.9)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *_a: source)
    rows = api._strategy_scan_wrapper("Dip Buy", send_email=False, publish_generic_cache=False)
    assert rows
    assert api._infer_alert_direction(rows[0]) == "LONG"
    assert rows[0]["Signal_Direction"].upper() == "LONG"
    if isinstance(rows[0].get("trade_setup"), dict):
        assert rows[0]["trade_setup"]["direction"] == "LONG"


@pytest.mark.parametrize("ratio", [.59999, 1.50001])
def test_hidden_dip_buy_does_not_round_a_fallback_rvol_inside_its_filter(monkeypatch, ratio):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture, _metrics
    _wrapper_fixture(monkeypatch, price=95., metrics=_metrics(rvol20=None))
    source = api._fetch_strategy_snapshot_universe("Dip Buy")
    source[0]["prevDay"].update(c=100., v=1_000_000)
    source[0]["day"].update(o=95., h=95.1, l=94.9, v=ratio*1_000_000)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *_a: source)
    rows = api._strategy_scan_wrapper("Dip Buy", send_email=False, publish_generic_cache=False)
    assert rows == []


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_hidden_harmonic_whole_wrapper_keeps_pattern_and_native_direction_aligned(monkeypatch, side):
    from test_deep_audit_shared_fixes import _piecewise
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    bars = _piecewise([(0,111),(165,110),(170,100),(180,140),(190,115.28),(200,130.5),(210,108.56),(219,109.5)],220)
    if side == "SHORT":
        bars = [dict(b, open=250-b["open"], high=250-b["low"], low=250-b["high"], close=250-b["close"]) for b in bars]
    postfilter = api._apply_special_strategy_post_filter
    _wrapper_fixture(monkeypatch, price=bars[-1]["close"])
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", postfilter)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *_a, **_k: bars)
    prefix = "Harmonic Bullish" if side == "LONG" else "Harmonic Bearish"
    name = next(n for n in api.get_strategies_for_market("stocks") if n.startswith(prefix))
    rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    assert rows and rows[0]["harmonic_direction"] == side
    assert api._infer_alert_direction(rows[0]) == side
    if isinstance(rows[0].get("trade_setup"), dict):
        assert rows[0]["trade_setup"]["direction"] == side


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_hidden_volume_void_whole_wrapper_keeps_void_and_native_direction_aligned(monkeypatch, side):
    from test_deep_audit_shared_fixes import _piecewise
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    bars = _piecewise([(0,100.),(39,100.),(49,110.),(79,110.),(89,100.)],90)
    bars = [dict(b, volume=1_000_000 if b["close"] in (100.,110.) else 1000.) for b in bars]
    if side == "SHORT":
        bars = [dict(b, open=200-b["open"], high=200-b["low"], low=200-b["high"], close=200-b["close"]) for b in bars]
    postfilter = api._apply_special_strategy_post_filter
    _wrapper_fixture(monkeypatch, price=bars[-1]["close"])
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", postfilter)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *_a, **_k: bars)
    prefix = "Volume Void Long" if side == "LONG" else "Volume Void Short"
    name = next(n for n in api.get_strategies_for_market("stocks") if n.startswith(prefix))
    rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    assert rows and rows[0]["void_target"]
    assert api._infer_alert_direction(rows[0]) == side
    if isinstance(rows[0].get("trade_setup"), dict):
        assert rows[0]["trade_setup"]["direction"] == side


def _churn_actual_metrics_fixture(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    metrics = api._strategy_daily_history_metrics
    postfilter = api._apply_special_strategy_post_filter
    _wrapper_fixture(monkeypatch, price=100.)
    cutoff = datetime(2026,9,23,20,15,tzinfo=timezone.utc)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz) if tz else cutoff.replace(tzinfo=None)
    monkeypatch.setattr(api, "datetime", Clock)
    sessions = []
    day = cutoff.astimezone(api.stock_swing.NY).date()
    while len(sessions) < 70:
        close = api.stock_swing.session_close(day.isoformat())
        if close is not None and close <= cutoff:
            sessions.append(day.isoformat())
        day -= timedelta(days=1)
    sessions.sort()
    bars = []
    for i, session in enumerate(sessions):
        price = 100. + (69-i)*.01
        bars.append(dict(date=session, open=price, high=price+.05, low=price-.05,
                         close=price, volume=200_000_000. if i == 69 else 1_000_000.))
    source = api._fetch_strategy_snapshot_universe("High Volume Churn ")
    source[0]["day"].update(o=100., h=100.05, l=99.95, c=100., v=200_000_000.)
    source[0]["prevDay"].update(c=100.01, v=1_000_000.)
    source[0]["lastTrade"].update(t=int(cutoff.timestamp()*1_000_000_000))
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *_a: source)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *_a, **_k: bars)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", metrics)
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", postfilter)
    return cutoff, bars


def test_actual_daily_metrics_do_not_clip_200x_volume_to_a_50x_filter_boundary(monkeypatch):
    cutoff, bars = _churn_actual_metrics_fixture(monkeypatch)
    metrics = api._strategy_daily_history_metrics(bars, price=100., day_open=100.,
        day_high=100.05, day_low=99.95, day_volume=200_000_000., now_utc=cutoff,
        include_structure=False)
    assert metrics["avg_vol20"] == 1_000_000.
    assert metrics["rvol20"] == pytest.approx(200.)


def test_actual_churn_wrapper_does_not_clip_200x_rvol_into_50x_admission(monkeypatch):
    _churn_actual_metrics_fixture(monkeypatch)
    name = next(n for n in api.get_strategies_for_market("stocks") if n.startswith("High Volume Churn"))
    assert api.STRATEGIES[name]["filters"]["RVOL"][1] == 50.
    rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    assert rows == []


@pytest.mark.parametrize("prefix", ["Long Wick Up", "Long Wick Down"])
def test_removed_wick_labels_cannot_accept_a_candle_with_no_wicks(monkeypatch, prefix):
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    _wrapper_fixture(monkeypatch, price=100.)
    name = next(n for n in api.get_strategies_for_market("stocks") if n.startswith(prefix))
    source = api._fetch_strategy_snapshot_universe(name)
    source[0]["day"].update(o=99.5, h=100., l=99.5, c=100.)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *_a: source)
    try:
        rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    except api.HTTPException as exc:
        assert exc.status_code == 501
        return
    assert rows == []


def test_removed_all_harmonic_cannot_label_a_real_bearish_pattern_as_long(monkeypatch):
    from test_deep_audit_shared_fixes import _piecewise
    from test_stock_momentum_confirmed_contract import _wrapper_fixture
    bars = _piecewise([(0,111),(165,110),(170,100),(180,140),(190,115.28),(200,130.5),(210,108.56),(219,109.5)],220)
    bars = [dict(b, open=250-b["open"], high=250-b["low"], low=250-b["high"], close=250-b["close"]) for b in bars]
    postfilter = api._apply_special_strategy_post_filter
    _wrapper_fixture(monkeypatch, price=bars[-1]["close"])
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", postfilter)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *_a, **_k: bars)
    name = next(n for n in api.get_strategies_for_market("stocks") if n.startswith("Harmonic All Patterns"))
    try:
        rows = api._strategy_scan_wrapper(name, send_email=False, publish_generic_cache=False)
    except api.HTTPException as exc:
        assert exc.status_code == 501
        return
    assert rows and rows[0]["harmonic_direction"] == "SHORT"
    assert api._infer_alert_direction(rows[0]) == "SHORT"


@pytest.mark.parametrize("name", ["Insider Buying", "Insider Selling"])
def test_unimplemented_insider_cannot_reuse_a_different_cache(monkeypatch, name):
    monkeypatch.setattr(api, "load_live_cache_file", lambda *_a, **_k: pytest.fail("cache I/O before admission"))
    with pytest.raises(api.HTTPException) as caught:
        api.get_scan_results(strategy=name, market_type="stocks")
    assert caught.value.status_code == 501
    with pytest.raises(ValueError, match="structure_reminder_scanner_not_supported"):
        api._structure_reminder_server_row("PROBE", name, "LONG")
    assert api._evaluate_trade_reminder(dict(asset_type="stock", scanner=name))["triggered"] is False


@pytest.mark.parametrize("name", ["Insider Buying", "Insider Selling"])
def test_legacy_insider_rows_cannot_receive_trade_mail_permission(name):
    state = api._classify_alert_candidate("stock_strategy", {"Strategy": name, "Ticker": "PROBE"})
    assert state["alertable_now"] is False
    assert "stock_strategy_not_implemented" in state["suppression_reasons"]
    assert state["decision"] == "NO_TRADE"


def test_hidden_menu_explicitly_marks_unimplemented_insider_source():
    rows = api.get_public_strategies_for_market("stocks", include_hidden=True)
    for name in ("Insider Buying", "Insider Selling"):
        assert rows[name]["scan_supported"] is False


def test_legacy_premarket_insider_cannot_bypass_the_normal_gate(monkeypatch):
    state = api._classify_premarket_candidate("stock_strategy", {"Strategy": "Insider Buying", "Premarket": True})
    assert state["alertable_now"] is False
    assert "stock_strategy_not_implemented" in state["suppression_reasons"]
    calls = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args: calls.append(args))
    monkeypatch.setattr(api, "_classify_premarket_candidate", lambda *_a: pytest.fail("unsupported producer reached classifier"))
    api._send_strategy_scan_alerts("Insider Buying", [{"ticker": "PROBE", "Premarket": True}])
    assert calls == [("stock_strategy", {"stock_strategy_not_implemented": 1})]
