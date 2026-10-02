"""Offline causal-input regressions discovered by the 02 October deep audit.

Run through scripts/run_offline_tests.py: no live HTTP, SMTP,
credentials, or production state. Tests assert the desired data contract,
not historical profitability; initially failing cases identify current bugs.
"""
import builtins
import copy
import io
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import api
import modules.new_listing_scanner as listing
from test_new_listing_deep_audit import (
    _deep_book, _fresh_candles, _fresh_ticker, _micro_crack_candles,
    _with_causal_listing_vrvp,
)
from test_crypto_explosion_scanner import _bars as _explosion_bars, _candidate, _btc_context
from test_early_movers_audit import _btc, _volume_coin, _perp, _TrendingResponse


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("Unmocked network forbidden in deep crypto audit")
    monkeypatch.setattr(api.req, "get", blocked)
    monkeypatch.setattr(listing, "_api_get", blocked)


@pytest.mark.parametrize("raw", ["nan", "inf", "bad", True, False])
def test_invalid_btc_change_never_becomes_measured_flat_tailwind(monkeypatch, raw):
    monkeypatch.setattr(api, "_CE_BTC_CONTEXT_CACHE", {"ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [
        {"id": "bitcoin", "price_change_percentage_24h": raw,
         "last_updated": "2026-10-02T12:00:00Z"},
    ])
    result = api._get_crypto_btc_context_locked("TEST", 8)
    assert result["known"] is False
    assert result["tailwind"] is False


def test_old_btc_provider_observation_is_not_refreshed_by_fetch_receipt(monkeypatch):
    monkeypatch.setattr(api, "_CE_BTC_CONTEXT_CACHE", {"ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [
        {"id": "bitcoin", "price_change_percentage_24h": 0.5,
         "last_updated": "2020-01-01T00:00:00Z"},
    ])
    result = api._get_crypto_btc_context_locked("TEST", 8)
    assert result["known"] is False
    assert result["tailwind"] is False


@pytest.mark.parametrize("btc", [
    {"btc_24h": None, "btc_7d": None, "tailwind": True},
    {"btc_24h": "nan", "btc_7d": "bad", "tailwind": True},
    {"btc_24h": True, "btc_7d": False, "tailwind": True},
    {"btc_24h": 0.0, "btc_7d": 0.0, "known": False, "tailwind": True},
])
def test_early_mover_mail_context_requires_measured_btc_values(btc):
    fields = api._extract_early_mover_fields({"btc_context": btc})
    assert fields["btc_context_known"] is False
    assert api._early_mover_btc_allows_long(fields) is False


def test_coingecko_one_page_cache_does_not_satisfy_four_page_scan(monkeypatch):
    now = time.time()
    cache_path = "/tmp/coingecko_markets_cache.json"
    cached = {"coins": [{"id": f"cached-{i}"} for i in range(250)], "pages": 1}
    real_open = builtins.open
    real_exists = api.os.path.exists
    real_mtime = api.os.path.getmtime
    calls = []

    def open_cache(path, mode="r", *args, **kwargs):
        if str(path) == cache_path:
            return io.StringIO(json.dumps(cached)) if "r" in mode else io.StringIO()
        return real_open(path, mode, *args, **kwargs)

    def get(_url, params=None, **_kw):
        page = params["page"]
        calls.append(page)
        return SimpleNamespace(status_code=200, json=lambda: [
            {"id": f"live-{page}-{i}"} for i in range(250)
        ])

    monkeypatch.setattr(builtins, "open", open_cache)
    monkeypatch.setattr(api.os.path, "exists", lambda p: True if str(p) == cache_path else real_exists(p))
    monkeypatch.setattr(api.os.path, "getmtime", lambda p: now if str(p) == cache_path else real_mtime(p))
    monkeypatch.setattr(api.req, "get", get)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)
    result = api._fetch_coingecko_markets(pages=4)
    assert len(result) == 1000
    assert calls == [1, 2, 3, 4]


def _htf_rows():
    now = int(time.time())
    return [{"timestamp": now - (i + 2) * 14400, "open": 10,
             "high": 10.2, "low": 9.9, "close": 10.1, "volume": 1000}
            for i in reversed(range(12))]


def test_early_mover_htf_execution_cannot_accept_years_old_context():
    bars = _htf_rows()
    for row in bars:
        row["timestamp"] -= 365 * 86400
    result = api._early_mover_htf_execution_context({}, bars, "4h")
    assert result["ok"] is False


def test_early_mover_htf_execution_cannot_use_future_candles():
    bars = _htf_rows()
    for row in bars:
        row["timestamp"] += 365 * 86400
    result = api._early_mover_htf_execution_context({}, bars, "4h")
    assert result["ok"] is False


def test_early_mover_htf_execution_cannot_accept_impossible_geometry():
    bars = _htf_rows()
    for row in bars:
        row["close"] = 20  # Above the measured high=10.2, impossible OHLC.
    result = api._early_mover_htf_execution_context({}, bars, "4h")
    assert result["ok"] is False


def test_micro_crack_ignores_conflicting_timestamp_observation():
    candles = _micro_crack_candles()
    duplicate = dict(candles[-1], open=114.4, close=116.0)
    baseline = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert baseline["micro_trigger_ok"] is True
    result = listing.calculate_micro_crack_trigger(candles + [duplicate], {"ath": 130})
    assert result["micro_trigger_ok"] is False


def test_micro_crack_cannot_use_impossible_signal_bar():
    candles = _micro_crack_candles()
    candles[-1]["low"] = 150  # low > high and low > close.
    result = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert result["micro_trigger_ok"] is False


def test_listing_safety_rejects_future_ticker_clock():
    ticker = _fresh_ticker()
    ticker["timestamp"] = int(time.time()) + 3600
    safe, _warnings = listing.check_safety(ticker, _deep_book(), _fresh_candles())
    assert safe is False


def test_listing_micro_duplicate_order_does_not_select_different_winner():
    candles = _micro_crack_candles()
    duplicate = dict(candles[-1], open=114.4, close=116.0)
    prefix = candles[:-1]
    left = listing.calculate_micro_crack_trigger(prefix + [candles[-1], duplicate], {"ath": 130})
    right = listing.calculate_micro_crack_trigger(prefix + [duplicate, candles[-1]], {"ath": 130})
    assert left["micro_trigger_ok"] == right["micro_trigger_ok"]
    assert left["micro_current_price"] == right["micro_current_price"]


@pytest.mark.parametrize("unit", [1, 1000, 1000000, 1000000000])
def test_valid_listing_micro_units_and_order_preserve_confirmed_crack(unit):
    candles = _micro_crack_candles()
    baseline = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert baseline["micro_trigger_ok"] is True
    encoded = [dict(row, timestamp=row["timestamp"] * unit) for row in reversed(candles)]
    result = listing.calculate_micro_crack_trigger(encoded, {"ath": 130})
    for field in ("micro_trigger_ok", "micro_current_price", "micro_stop_loss", "micro_score"):
        assert result[field] == baseline[field]


def test_identical_listing_micro_observations_do_not_count_as_extra_bars():
    candles = _micro_crack_candles()
    expected = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    repeated = candles + [dict(row) for row in reversed(candles)]
    result = listing.calculate_micro_crack_trigger(repeated, {"ath": 130})
    for field in ("micro_trigger_ok", "micro_current_price", "micro_stop_loss", "micro_score"):
        assert result[field] == expected[field]


@pytest.mark.parametrize("corruption", [
    {"close": True}, {"high": "inf"}, {"volume_usd": "nan"},
    {"volume_usd": -1}, {"close": None}, {"c": 999},
    {"completed": False}, {"partial": True}, {"confirm": "false"},
])
def test_invalid_completed_micro_signal_observation_is_never_actionable(corruption):
    candles = _micro_crack_candles()
    candles[-1].update(corruption)
    result = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert result["micro_trigger_ok"] is False


def test_listing_safety_preserves_positive_fresh_control():
    safe, warnings = listing.check_safety(_fresh_ticker(), _deep_book(), _fresh_candles())
    assert safe is True, warnings


@pytest.mark.parametrize("bad_clock", [True, False, "nan", "inf", "bad", None])
def test_listing_safety_invalid_ticker_clock_is_unknown(bad_clock):
    ticker = dict(_fresh_ticker(), timestamp=bad_clock)
    safe, _warnings = listing.check_safety(ticker, _deep_book(), _fresh_candles())
    assert safe is False


def test_micro_trigger_does_not_treat_gap_as_consecutive_five_minute_bars():
    candles = _micro_crack_candles()
    assert listing.calculate_micro_crack_trigger(candles, {"ath": 130})["micro_trigger_ok"] is True
    del candles[-8]
    result = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert result["micro_trigger_ok"] is False


def test_micro_trigger_requires_measured_micro_volume_not_missing_zero_default():
    candles = _micro_crack_candles()
    for row in candles:
        row.pop("volume_usd")
    result = listing.calculate_micro_crack_trigger(candles, {"ath": 130})
    assert result["micro_trigger_ok"] is False


def test_listing_exhaustion_never_uses_current_forming_hour_for_pattern_evidence():
    now = int(time.time())
    candles = [dict(row, timestamp=now - (len(_micro_crack_candles()) - index + 1) * 3600)
               for index, row in enumerate(_micro_crack_candles())]
    cutoff = datetime.fromtimestamp(now, timezone.utc)
    _, _, baseline = listing.calculate_listing_exhaustion(candles, _fresh_ticker(), as_of=cutoff)
    forming = {"timestamp": now - 60, "open": 114.5, "high": 300,
               "low": 70, "close": 75, "volume_usd": 500000, "is_closed": False}
    _, _, changed = listing.calculate_listing_exhaustion(candles + [forming], _fresh_ticker(), as_of=cutoff)
    for field in ("ath", "current_price", "pump_pct", "from_ath_pct", "current_red_streak"):
        assert changed[field] == baseline[field]


def test_measured_zero_btc_context_is_known_not_missing(monkeypatch):
    updated_at = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(api, "_CE_BTC_CONTEXT_CACHE", {"ts": 0, "known": False})
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [
        {"id": "bitcoin", "price_change_percentage_24h": 0.0,
         "price_change_percentage_7d_in_currency": 0.0, "last_updated": updated_at},
    ])
    btc = api._get_crypto_btc_context_locked("TEST", 8)
    assert btc["known"] is True
    assert btc["btc_24h"] == 0.0
    fields = api._extract_early_mover_fields({"btc_context": {
        "known": True, "status": "ok", "btc_24h": 0.0, "btc_7d": 0.0,
        "observed_at": updated_at, "tailwind": True,
    }})
    assert fields["btc_context_known"] is True
    assert api._early_mover_btc_allows_long(fields) is True


def _hourly_listing_bars():
    now = int(time.time()) // 3600 * 3600
    rows = _micro_crack_candles()
    return [dict(row, timestamp=now - (len(rows) - index) * 3600)
            for index, row in enumerate(rows)]


def test_listing_btc_context_uses_exact_same_completed_hourly_window(monkeypatch):
    candles = _hourly_listing_bars()
    btc_bars = [dict(row, open=100, high=101, low=99, close=100) for row in candles]
    monkeypatch.setattr(listing, "fetch_binance_candles", lambda *_a, **_kw: btc_bars)
    _, _, result = listing.calculate_listing_exhaustion(candles, _fresh_ticker())
    assert result["btc_context_known"] is True
    assert result["btc_change_pct"] == 0.0
    assert result["btc_context_completed_at"] == candles[-1]["timestamp"] + 3600
    assert result["btc_context_opened_at"] == candles[0]["timestamp"]


@pytest.mark.parametrize("corruption", ["different_window", "stale", "missing_interval", "invalid_ohlc", "future"])
def test_listing_btc_context_never_equates_length_with_synchronous_evidence(monkeypatch, corruption):
    candles = _hourly_listing_bars()
    btc_bars = [dict(row, open=100, high=101, low=99, close=100) for row in candles]
    if corruption in {"different_window", "stale", "future"}:
        delta = {"different_window": -1800, "stale": -86400, "future": 86400}[corruption]
        btc_bars = [dict(row, timestamp=row["timestamp"] + delta) for row in btc_bars]
    elif corruption == "missing_interval":
        del btc_bars[-5]
    else:
        btc_bars[-1]["close"] = 200  # impossible close above high
    monkeypatch.setattr(listing, "fetch_binance_candles", lambda *_a, **_kw: btc_bars)
    _, _, result = listing.calculate_listing_exhaustion(candles, _fresh_ticker())
    assert result["btc_context_known"] is False
    assert result["btc_short_context"] == "UNKNOWN"
    assert result.get("btc_change_pct") is None


@pytest.mark.parametrize("corruption", ["stale", "duplicate", "gap", "no_clock", "bool_price"])
def test_listing_exhaustion_invalid_hourly_evidence_never_becomes_score(monkeypatch, corruption):
    candles = _hourly_listing_bars()
    if corruption == "stale":
        candles = [dict(row, timestamp=row["timestamp"] - 86400) for row in candles]
    elif corruption == "duplicate":
        candles.append(dict(candles[-1], close=candles[-1]["close"] + 0.1))
    elif corruption == "gap":
        del candles[-5]
    elif corruption == "no_clock":
        candles[-1].pop("timestamp")
    else:
        candles[-1]["close"] = True
    score, _details, result = listing.calculate_listing_exhaustion(candles, _fresh_ticker())
    assert score == 0
    assert result["hourly_data_status"] != "ok"
    assert result["btc_context_known"] is False


def test_listing_generator_unknown_btc_is_watch_not_known_neutral():
    pump = _with_causal_listing_vrvp({
        "ath": 100, "current_price": 97, "pump_pct": 80, "from_ath_pct": 3.0,
        "momentum_recent": -0.8, "current_red_streak": 1, "avg_upper_wick_pct": 25,
        "micro_trigger_ok": True, "micro_score": 75, "micro_stop_loss": 101,
        "listing_source": "new_listing", "listing_age_hours": 24,
    })
    positive = listing.generate_short_signal("TESTUSDT", pump, 85, [], True, [])
    assert listing._is_tradeable_short_signal(positive) is True
    pump.update(btc_context_known=False, btc_context_status="missing_or_invalid_synchronous_window")
    negative = listing.generate_short_signal("TESTUSDT", pump, 85, [], True, [])
    assert negative["btc_context_ok"] is False
    assert "btc_context_missing" in negative["risk_flags"]
    assert "btc_risk_on_wait_for_deeper_crack" not in negative["risk_flags"]
    assert listing._is_tradeable_short_signal(negative) is False


def test_early_mover_armed_context_does_not_accept_year_old_higher_timeframe():
    bars = _htf_rows()
    for row in bars:
        row.update(open=10.05, high=10.1, low=10.0, close=10.05)
    assert api._early_mover_htf_armed_context({}, bars, "4h")["armed_ok"] is True
    for row in bars:
        row["timestamp"] -= 365 * 86400
    assert api._early_mover_htf_armed_context({}, bars, "4h")["armed_ok"] is False


@pytest.mark.parametrize("stale_timeframe", ["15m", "4h"])
def test_explosion_requires_fresh_range_and_higher_timeframe_before_trade_signal(monkeypatch, stale_timeframe):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda _symbol, change: _btc_context(change))
    def verified_setup(setup, *_a, **_kw):
        entry = setup["entry"]
        return {**setup, "stop": entry - 0.2, "tp1": entry + 0.4, "tp2": entry + 0.6,
                "tp1_is_projection": False, "tp2_is_projection": False,
                "target_quality": "STRUCTURAL_FIRST_BARRIER", "barrier_gate": "NONE",
                "barrier_gate_active": False, "structure_status": "ACCEPT"}
    monkeypatch.setattr(api, "apply_vrvp_to_trade_setup", verified_setup)
    bars5 = _explosion_bars(90, start=9.50, step=0.004, volume=1000, last={
        "open": 10.00, "high": 10.14, "low": 9.98, "close": 10.12, "volume": 3200})
    bars15 = _explosion_bars(60, start=9.42, step=0.009, volume=3000, interval=900, last={
        "open": 9.95, "high": 10.02, "low": 9.91, "close": 9.98, "volume": 3000})
    bars4h = _explosion_bars(60, start=9.4, step=0.006, volume=5000, interval=14400)
    baseline = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8), bars5, bars15, bars4h)
    assert baseline is not None
    assert baseline["trade_signal"] == "JETZT_TRADEN"
    for row in bars15 if stale_timeframe == "15m" else bars4h:
        row["timestamp"] -= 365 * 86400
    stale = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8), bars5, bars15, bars4h)
    assert stale is None or stale["trade_signal"] != "JETZT_TRADEN"


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), True])
def test_listing_safety_depth_cannot_be_non_measured_or_non_finite(bad_value):
    book = {"bids": [(96.9, bad_value)], "asks": [(97.1, bad_value)]}
    safe, _warnings = listing.check_safety(_fresh_ticker(), book, _fresh_candles())
    assert safe is False


def test_listing_safety_boolean_book_price_is_not_one_dollar_measurement():
    book = {"bids": [(True, 20000)], "asks": [(True, 20000)]}
    safe, _warnings = listing.check_safety(_fresh_ticker(), book, _fresh_candles())
    assert safe is False


def _install_listing_producer_fixture(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    state = {"binance:TESTUSDT": {
        "symbol": "TESTUSDT", "exchange": "binance", "source": "new_listing",
        "status": "monitoring", "detected_at": now.isoformat(),
        "listing_time": datetime.fromtimestamp(now.timestamp() - 24 * 3600, timezone.utc).isoformat(),
    }}
    monkeypatch.setattr(listing, "RESULTS_FILE", tmp_path / "producer_results.json")
    monkeypatch.setattr(listing, "load_monitoring_list", lambda: copy.deepcopy(state))
    def save_state(current):
        state.clear()
        state.update(copy.deepcopy(current))
    monkeypatch.setattr(listing, "save_monitoring_list", save_state)
    monkeypatch.setattr(listing, "detect_new_listings", lambda: ([], [], []))
    monkeypatch.setattr(listing, "detect_active_pumps", lambda _perps: [])
    monkeypatch.setattr(listing.time, "sleep", lambda _seconds: None)
    ticker = dict(_fresh_ticker(), price=97)
    monkeypatch.setattr(listing, "fetch_ticker_for", lambda *_a, **_kw: dict(ticker))
    monkeypatch.setattr(listing, "fetch_orderbook_for", lambda *_a, **_kw: _deep_book())
    monkeypatch.setattr(listing, "fetch_candles_for", lambda *_a, **_kw: _hourly_listing_bars())
    pump = _with_causal_listing_vrvp({
        "ath": 100, "current_price": 97, "pump_pct": 80, "from_ath_pct": 3.0,
        "momentum_recent": -0.8, "current_red_streak": 1, "avg_upper_wick_pct": 25,
        "micro_trigger_ok": True, "micro_score": 75, "micro_stop_loss": 101,
        "listing_source": "new_listing", "listing_age_hours": 24,
    })
    monkeypatch.setattr(listing, "calculate_listing_exhaustion", lambda *_a, **_kw: (85, [], copy.deepcopy(pump)))
    monkeypatch.setattr(listing, "check_safety", lambda *_a, **_kw: (True, []))
    monkeypatch.setattr(listing, "calculate_micro_crack_trigger", lambda *_a, **_kw: {
        "micro_trigger_ok": True, "micro_score": 75, "micro_current_price": 97,
        "micro_stop_loss": 101, "micro_candle_closed_at": int(now.timestamp()) - 300,
        "micro_data_age_seconds": 300,
    })
    return state, pump, ticker


def test_listing_producer_retry_must_not_depend_on_pre_smtp_signal_status(monkeypatch, tmp_path):
    """Detection is not acceptance: a valid failed first delivery may retry."""
    state, _pump, _ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    first = listing.run_new_listing_scanner()
    assert len(first["signals"]) == 1
    assert state["binance:TESTUSDT"]["status"] == "signal"
    # Simulate an unaccepted first mail: no accepted delivery or trade tracker
    # was written. The next producer observation must preserve its retry input.
    retry = listing.run_new_listing_scanner()
    assert len(retry["signals"]) == 1
    assert retry["signals"][0]["producer_episode_id"] == first["signals"][0]["producer_episode_id"]
    assert retry["signals"][0]["delivery_retry_candidate"] is True
    assert state["binance:TESTUSDT"]["signal_entry"] == first["signals"][0]["signal"]["entry"]
    assert state["binance:TESTUSDT"]["signal_at"] == first["signals"][0]["producer_episode_started_at"]


@pytest.mark.parametrize("lost_evidence", ["btc", "safety", "micro", "stop", "expired"])
def test_listing_producer_retry_never_bypasses_current_invalidity(monkeypatch, tmp_path, lost_evidence):
    state, pump, ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    first = listing.run_new_listing_scanner()
    assert len(first["signals"]) == 1
    if lost_evidence == "btc":
        pump["btc_context_known"] = False
        pump["btc_context_status"] = "missing"
    elif lost_evidence == "safety":
        monkeypatch.setattr(listing, "check_safety", lambda *_a, **_kw: (False, ["missing_orderbook"]))
    elif lost_evidence == "micro":
        monkeypatch.setattr(listing, "calculate_micro_crack_trigger", lambda *_a, **_kw: {
            "micro_trigger_ok": False, "micro_score": 0, "micro_current_price": 97,
        })
    elif lost_evidence == "stop":
        ticker["price"] = state["binance:TESTUSDT"]["signal_stop_loss"] + 1
    else:
        state["binance:TESTUSDT"]["signal_at"] = datetime.fromtimestamp(
            time.time() - (listing.SIGNAL_EXPIRY_HOURS + 1) * 3600, timezone.utc,
        ).isoformat()
    assert listing.run_new_listing_scanner()["signals"] == []


def _episode_identity_row(exchange="binance", contract="TESTUSDT", started=None):
    started = started or datetime.fromtimestamp(time.time() - 3600, timezone.utc)
    return {
        "symbol": contract, "contract": contract, "exchange": exchange,
        "direction": "SHORT", "strategy": "new_listing_dump", "timeframe": "5m",
        "entry": 97, "stop": 101, "tp1": 90, "tp2": 86,
        "producer_episode_id": listing._producer_episode_id(exchange, contract, started),
        "producer_episode_started_at": started.isoformat(),
        "producer_episode_model": "new_listing_episode_v4",
    }


def test_new_listing_valid_episode_identity_is_stable_across_quote_geometry_changes():
    row = _episode_identity_row()
    changed = dict(row, entry=96.8, stop=100.7, tp1=89.5, tp2=85.6)
    assert api._alert_signal_identity_key("new_listing", row) == api._alert_signal_identity_key("new_listing", changed)


@pytest.mark.parametrize("first_delivery_accepted,delivery_outcome", [
    (True, "accepted"), (False, "refused"), (False, "unknown"),
])
def test_listing_producer_mail_retry_respects_real_dedupe_transition(monkeypatch, tmp_path, first_delivery_accepted, delivery_outcome):
    """Real producer and API claim/dedupe; only the wire is simulated.

    An accepted result closes delivery once, an unaccepted result releases the
    claim. Neither result is written into the producer as pretend acceptance.
    """
    state, _pump, _ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "episode_mail_dedupe.json"))
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_filter_open_equivalent_trade_rows", lambda _name, rows, **_kw: (rows, 0))
    monkeypatch.setattr(api, "_revalidate_new_listing_mail_candidate", lambda alert, **_kw: {"ok": True, "candidate": alert})
    monkeypatch.setattr(api, "_last_delivery_outcome", lambda: delivery_outcome)
    wire_attempts = []
    def simulated_wire(_subject, _body, **kwargs):
        wire_attempts.append(kwargs["delivery_dedupe_keys"][0])
        return first_delivery_accepted if len(wire_attempts) == 1 else True
    monkeypatch.setattr(api, "_send_email_alert", simulated_wire)
    first = listing.run_new_listing_scanner()
    api._stamp_fresh_crypto_profile_contract(first, new_listing=True)
    api._send_new_listing_pipeline_alerts(first)
    assert len(wire_attempts) == 1
    # New derived geometry must not create another identity in the same episode.
    second = listing.run_new_listing_scanner()
    assert len(second["signals"]) == 1
    second["signals"][0]["signal"]["entry"] -= 0.1
    api._stamp_fresh_crypto_profile_contract(second, new_listing=True)
    api._send_new_listing_pipeline_alerts(second)
    expected_attempts = 1 if first_delivery_accepted or delivery_outcome == "unknown" else 2
    assert len(wire_attempts) == expected_attempts
    api._send_new_listing_pipeline_alerts(second)
    assert len(wire_attempts) == expected_attempts
    assert len(set(wire_attempts)) == 1
    assert state["binance:TESTUSDT"]["status"] == "signal"


@pytest.mark.parametrize("variant", ["venue", "contract", "episode"])
def test_new_listing_episode_identity_keeps_native_economic_opportunities_distinct(variant):
    row = _episode_identity_row()
    if variant == "venue":
        changed = _episode_identity_row(exchange="mexc")
    elif variant == "contract":
        changed = _episode_identity_row(contract="TESTUSDC")
    else:
        changed = _episode_identity_row(started=datetime.fromtimestamp(time.time() - 1800, timezone.utc))
    assert api._alert_signal_identity_key("new_listing", row) != api._alert_signal_identity_key("new_listing", changed)


@pytest.mark.parametrize("corruption", ["malformed", "future", "different_contract", "legacy_model"])
def test_invalid_new_listing_episode_proof_cannot_erase_plan_geometry(corruption):
    row = _episode_identity_row()
    if corruption == "malformed":
        row["producer_episode_id"] = "nls4:invalid"
    elif corruption == "future":
        row["producer_episode_started_at"] = datetime.fromtimestamp(time.time() + 3600, timezone.utc).isoformat()
    elif corruption == "different_contract":
        row["producer_episode_id"] = listing._producer_episode_id("binance", "OTHERUSDT", row["producer_episode_started_at"])
    else:
        row["producer_episode_model"] = "new_listing_episode_v3"
    changed = dict(row, entry=96.8)
    assert api._alert_signal_identity_key("new_listing", row) != api._alert_signal_identity_key("new_listing", changed)


def test_new_listing_flattener_preserves_measured_btc_and_episode_provenance():
    entry = _episode_identity_row()
    sig = {
        **entry, "stop_loss": 101, "btc_context_known": True, "btc_context_status": "ok",
        "btc_context_ok": True, "pump_data": {
            "current_price": 97, "btc_context_source": "binance:BTCUSDT:1H",
            "btc_context_opened_at": int(time.time()) - 24 * 3600,
            "btc_context_completed_at": int(time.time()) - 3600,
        },
    }
    flat = api._flatten_new_listing_pipeline_results({"signals": [{**entry, "signal": sig}]})
    assert len(flat) == 1
    row = flat[0]
    assert row["btc_context_known"] is True
    assert row["btc_context_status"] == "ok"
    assert row["btc_context_source"] == sig["pump_data"]["btc_context_source"]
    assert row["btc_context_completed_at"] == sig["pump_data"]["btc_context_completed_at"]
    assert row["producer_episode_id"] == entry["producer_episode_id"]
    assert row["producer_episode_started_at"] == entry["producer_episode_started_at"]
    assert row["producer_episode_model"] == entry["producer_episode_model"]


def test_listing_episode_id_binds_venue_native_contract_and_original_clock():
    started = datetime.fromtimestamp(time.time() - 3600, timezone.utc)
    proof = listing._producer_episode_id("binance", "TESTUSDT", started)
    assert proof.startswith("nls4:") and len(proof) == 69
    assert proof == listing._producer_episode_id("BINANCE", "testusdt", started.isoformat())
    assert proof != listing._producer_episode_id("mexc", "TEST_USDT", started)
    assert proof != listing._producer_episode_id("binance", "TESTUSDC", started)
    assert proof != listing._producer_episode_id("binance", "TESTUSDT", started.timestamp() - 3600)
    assert listing._producer_episode_id("spot", "TESTUSDT", started) is None
    assert listing._producer_episode_id("binance", "TESTUSDT", time.time() + 3600) is None


@pytest.mark.parametrize("raw_change,btc_change,expected_phase", [
    (-2.999, -2.99, 1), (0.001, 0.0, 3),
])
def test_early_mover_phase_decision_uses_unrounded_measured_return(monkeypatch, raw_change, btc_change, expected_phase):
    btc = dict(_btc(change_24h=btc_change), last_updated=datetime.now(timezone.utc).isoformat())
    coin = dict(_volume_coin(change_24h=raw_change), last_updated=datetime.now(timezone.utc).isoformat())
    if raw_change > 0:
        coin["price_change_percentage_7d_in_currency"] = 30.0
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [btc, coin])
    monkeypatch.setattr(api.req, "get", lambda *_a, **_kw: _TrendingResponse())
    monkeypatch.setattr(api, "_verify_early_mover_intraday_trigger", lambda *_a, **_kw: {
        "ok": False, "reason": "no_fresh_5m_trigger",
    })
    result = api.fetch_early_movers(_prefetched_perps=_perp())
    rows = [row for row in result["coins"] if row["Symbol"] == "TVOL"]
    assert rows, "A raw Phase-1 observation may not disappear after display rounding"
    row = rows[0]
    measured = api._classify_phase(raw_change, coin["price_change_percentage_7d_in_currency"],
                                  coin["total_volume"] / coin["market_cap"] * 100, btc_change)
    assert measured[0] == expected_phase
    assert row["phase"] == expected_phase


def test_early_mover_positive_raw_alpha_is_not_rounded_into_distribution():
    entry = {
        "Symbol": "ALPHA", "Price": 1.0, "High24h": 1.05, "Low24h": 0.95,
        "MCap": 20_000_000, "Vol24h": 20_000_000, "VolMCapRatio": 100.0,
        "Change24h": 0.004, "Change7d": 1.0,
        "HasPerp": True, "PerpVolume24h": 10_000_000,
        "btc_context_known": True, "btc_observed_at": datetime.now(timezone.utc).isoformat(),
    }
    setup = api._build_early_mover_long_setup(entry, 1, 90, 0.0, 0.0)
    assert "turnover_without_alpha" not in setup["risk_flags"]
    assert setup["btc_context"]["alpha_24h"] > 0


@pytest.mark.parametrize("venue", ["binance", "bitget", "mexc", "cryptocom"])
@pytest.mark.parametrize("field", ["open", "volume", "volume_usd"])
def test_native_listing_provider_does_not_convert_raw_boolean_to_measurement(monkeypatch, venue, field):
    now = int(time.time())
    raw = {"timestamp": now - 600, "open": 1.0, "high": 1.02, "low": 0.99,
           "close": 1.01, "volume": 1000.0, "volume_usd": 1000.0}
    raw[field] = True
    array = [raw["timestamp"] * 1000, raw["open"], raw["high"], raw["low"],
             raw["close"], raw["volume"]]
    if venue == "binance":
        payload = [array + [(raw["timestamp"] + 300) * 1000 - 1, raw["volume_usd"]]]
    elif venue == "bitget":
        payload = {"data": [array + [raw["volume_usd"]]]}
    elif venue == "mexc":
        payload = {"success": True, "data": {
            "time": [raw["timestamp"]], "open": [raw["open"]], "high": [raw["high"]],
            "low": [raw["low"]], "close": [raw["close"]], "vol": [raw["volume"]],
            "amount": [raw["volume_usd"]],
        }}
    else:
        payload = {"result": {"data": [{"t": raw["timestamp"] * 1000,
            "o": raw["open"], "h": raw["high"], "l": raw["low"], "c": raw["close"],
            "v": raw["volume"], "vv": raw["volume_usd"]}]}}
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    normalized = getattr(listing, f"fetch_{venue}_candles")("TESTUSDT", "5m", 50)
    _, evidence = listing._completed_fresh_listing_candles(normalized, "5m")
    assert evidence.get("integrity_ok") is False


@pytest.mark.parametrize("venue", ["binance", "bitget", "mexc", "cryptocom"])
def test_native_listing_provider_valid_book_still_works(monkeypatch, venue):
    side = [["1.0", "20000"], ["0.999", "20000"]]
    other = [["1.001", "20000"], ["1.002", "20000"]]
    book = {"bids": side, "asks": other}
    payload = {"result": {"data": [book]}} if venue == "cryptocom" else {"success": True, "data": book} if venue in {"mexc", "bitget"} else book
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    assert getattr(listing, f"fetch_{venue}_orderbook")("TESTUSDT") == {
        "bids": [(1.0, 20000.0), (0.999, 20000.0)], "asks": [(1.001, 20000.0), (1.002, 20000.0)],
    }


@pytest.mark.parametrize("venue", ["binance", "bitget", "mexc", "cryptocom"])
def test_native_listing_provider_valid_numeric_string_candles_still_work(monkeypatch, venue):
    opened = int(time.time()) - 600
    array = [str(opened * 1000), "1.0", "1.02", "0.99", "1.01", "1000"]
    if venue == "binance":
        payload = [array + [str((opened + 300) * 1000 - 1), "1010"]]
    elif venue == "bitget":
        payload = {"data": [array + ["1010"]]}
    elif venue == "mexc":
        payload = {"success": True, "data": {"time": [str(opened)], "open": ["1.0"],
            "high": ["1.02"], "low": ["0.99"], "close": ["1.01"], "vol": ["1000"], "amount": ["1010"]}}
    else:
        payload = {"result": {"data": [{"t": str(opened * 1000), "o": "1.0", "h": "1.02",
            "l": "0.99", "c": "1.01", "v": "1000", "vv": "1010"}]}}
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    rows = getattr(listing, f"fetch_{venue}_candles")("TESTUSDT", "5m", 50)
    clean, evidence = listing._completed_fresh_listing_candles(rows, "5m")
    assert evidence["integrity_ok"] is True
    assert evidence["fresh"] is True
    assert clean[0]["timestamp"] == opened
    assert clean[0]["close"] == 1.01
    assert clean[0]["volume_usd"] == 1010


@pytest.mark.parametrize("venue", ["binance", "bitget", "mexc", "cryptocom"])
@pytest.mark.parametrize("bad_level", [[True, 50000], [1.0, "nan"], ["bad", 50000]])
def test_native_listing_provider_cannot_hide_corrupt_book_levels(monkeypatch, venue, bad_level):
    book = {"bids": [[1.0, 20000], bad_level], "asks": [[1.001, 20000], [1.002, 20000]]}
    payload = {"result": {"data": [book]}} if venue == "cryptocom" else {"success": True, "data": book} if venue in {"mexc", "bitget"} else book
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    assert getattr(listing, f"fetch_{venue}_orderbook")("TESTUSDT") is None


def test_listing_measured_divergence_does_not_round_through_btc_override(monkeypatch):
    now = int(time.time()) // 3600 * 3600
    coin = [{"timestamp": now - (24 - index) * 3600,
             "open": 100.0, "high": 101.0, "low": 96.0,
             "close": 97.001 if index == 23 else 100.0, "volume_usd": 100000}
            for index in range(24)]
    btc = [dict(row, open=100.0, high=103.0, low=99.0,
                close=102.0 if index == 23 else 100.0) for index, row in enumerate(coin)]
    monkeypatch.setattr(listing, "fetch_binance_candles", lambda *_a, **_kw: btc)
    _, _, observed = listing.calculate_listing_exhaustion(coin, _fresh_ticker())
    assert observed["btc_tailwind_risk"] is True
    assert observed["btc_divergence"] == pytest.approx(-4.999)
    assert observed["btc_divergence"] > listing.CONFIG["btc_tailwind_min_divergence_pct"]
    pump = _with_causal_listing_vrvp({
        "ath": 100, "current_price": 97, "pump_pct": 80, "from_ath_pct": 3.0,
        "momentum_recent": -0.8, "current_red_streak": 1, "avg_upper_wick_pct": 25,
        "micro_trigger_ok": True, "micro_score": 75, "micro_stop_loss": 101,
        "listing_source": "new_listing", "listing_age_hours": 24,
        **{key: observed[key] for key in (
            "btc_context_known", "btc_context_status", "btc_change_pct", "coin_change_pct",
            "btc_divergence", "btc_tailwind_risk", "btc_context_source", "btc_context_opened_at", "btc_context_completed_at",
        )},
    })
    signal = listing.generate_short_signal("TESTUSDT", pump, 85, [], True, [])
    assert signal is not None
    assert signal["btc_context_known"] is True
    assert signal["btc_context_ok"] is False
    assert signal["trade_signal"] != "JETZT_TRADEN"


@pytest.mark.parametrize("field", ["btc_change_pct", "btc_divergence"])
@pytest.mark.parametrize("invalid", [True, False, "nan", "inf", "bad", None])
def test_new_listing_cache_gate_requires_finite_raw_btc_not_flags_only(monkeypatch, tmp_path, field, invalid):
    _state, _pump, _ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    first = listing.run_new_listing_scanner()
    signal = first["signals"][0]["signal"]
    api._stamp_fresh_crypto_profile_contract(signal, new_listing=True)
    assert api._new_listing_short_safety_contract(signal) is True
    signal[field] = invalid
    signal["pump_data"][field] = invalid
    assert api._new_listing_short_safety_contract(signal) is False


@pytest.mark.parametrize("corruption", ["source", "missing_window", "future", "reversed", "stale", "short_window", "bool_clock", "inconsistent_delta"])
def test_new_listing_cache_gate_requires_causal_native_btc_window(monkeypatch, tmp_path, corruption):
    _state, _pump, _ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    signal = listing.run_new_listing_scanner()["signals"][0]["signal"]
    api._stamp_fresh_crypto_profile_contract(signal, new_listing=True)
    assert api._new_listing_short_safety_contract(signal) is True
    pump = signal["pump_data"]
    if corruption == "source":
        pump["btc_context_source"] = "unidentified:BTC:1H"
    elif corruption == "missing_window":
        pump.pop("btc_context_completed_at")
    elif corruption == "future":
        pump["btc_context_completed_at"] = time.time() + 3600
    elif corruption == "reversed":
        pump["btc_context_opened_at"] = pump["btc_context_completed_at"] + 3600
    elif corruption == "stale":
        pump["btc_context_opened_at"] -= 86400
        pump["btc_context_completed_at"] -= 86400
    elif corruption == "short_window":
        pump["btc_context_opened_at"] = pump["btc_context_completed_at"] - 3600
    elif corruption == "bool_clock":
        pump["btc_context_opened_at"] = True
    else:
        signal["btc_divergence"] = pump["btc_divergence"] = 25.0
    for key in ("btc_context_source", "btc_context_opened_at", "btc_context_completed_at"):
        signal.pop(key, None)
    assert api._new_listing_short_safety_contract(signal) is False
    assert listing._listing_btc_observation_valid(signal) is False


@pytest.mark.parametrize("field,invalid", [("btc_context_source", "other:BTC:1H"),
                                           ("btc_context_completed_at", True),
                                           ("btc_change_pct", 25.0)])
def test_new_listing_nested_btc_evidence_cannot_disagree_with_flat_copy(monkeypatch, tmp_path, field, invalid):
    _state, _pump, _ticker = _install_listing_producer_fixture(monkeypatch, tmp_path)
    signal = listing.run_new_listing_scanner()["signals"][0]["signal"]
    api._stamp_fresh_crypto_profile_contract(signal, new_listing=True)
    assert listing._listing_btc_observation_valid(signal) is True
    assert api._new_listing_short_safety_contract(signal) is True
    signal["pump_data"][field] = invalid
    assert listing._listing_btc_observation_valid(signal) is False
    assert api._new_listing_short_safety_contract(signal) is False


@pytest.mark.parametrize("venue", ["binance", "bitget", "mexc", "cryptocom"])
def test_native_listing_provider_retains_invalid_clock_for_integrity_check(monkeypatch, venue):
    opened = int(time.time()) - 600
    rows = [[opened * 1000, 1, 1.02, 0.99, 1.01, 1000,
             (opened + 300) * 1000 - 1, 1010],
            [True, 1, 1.02, 0.99, 1.01, 1000, 1010, 1010]]
    if venue == "binance":
        payload = rows
    elif venue == "bitget":
        payload = {"data": [row[:6] + [1010] for row in rows]}
    elif venue == "mexc":
        payload = {"success": True, "data": {key: value for key, value in zip(
            ["time", "open", "high", "low", "close", "vol", "amount"],
            [[opened, True], [1, 1], [1.02, 1.02], [0.99, 0.99], [1.01, 1.01], [1000, 1000], [1010, 1010]])}}
    else:
        payload = {"result": {"data": [{"t": row[0], "o": row[1], "h": row[2], "l": row[3],
                                        "c": row[4], "v": row[5], "vv": 1010} for row in rows]}}
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    normalized = getattr(listing, f"fetch_{venue}_candles")("TESTUSDT", "5m", 50)
    assert len(normalized) == 2
    _, evidence = listing._completed_fresh_listing_candles(normalized, "5m")
    assert evidence["integrity_ok"] is False


def test_cryptocom_measured_zero_quote_volume_is_not_replaced_by_proxy(monkeypatch):
    ticker = {"a": 1.0, "b": 0.999, "k": 1.001, "h": 1.02, "l": 0.99,
              "v": 1000000.0, "vv": 0.0, "c": 0.01, "t": int(time.time() * 1000)}
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: {"result": {"data": [ticker]}})
    normalized = listing.fetch_cryptocom_ticker("TEST_USDT")
    assert normalized["volume_usd_24h"] == 0.0
    safe, _reasons = listing.check_safety(normalized, _deep_book(), _fresh_candles())
    assert safe is False


@pytest.mark.parametrize("invalid", [True, "nan", "inf", "bad"])
def test_mexc_raw_invalid_contract_size_never_becomes_measured_oi_factor(monkeypatch, invalid):
    payload = {"success": True, "data": [{"symbol": "TEST_USDT", "quoteCoin": "USDT",
               "state": 0, "contractSize": invalid}]}
    monkeypatch.setattr(listing, "_MEXC_CONTRACT_SIZE_CACHE", {"ts": 0.0, "data": {}})
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: payload)
    assert listing.fetch_mexc_contract_sizes() == {}
    instruments = listing.fetch_mexc_futures_instruments()
    assert all(item["contract_size"] is None for item in instruments)


def test_cryptocom_quote_volume_proxy_remains_display_only(monkeypatch):
    ticker = {"a": 1.0, "b": 0.999, "k": 1.001, "h": 1.02, "l": 0.99,
              "v": 1000000.0, "c": 0.01, "t": int(time.time() * 1000)}
    monkeypatch.setattr(listing, "_api_get", lambda *_a, **_kw: {"result": {"data": [ticker]}})
    normalized = listing.fetch_cryptocom_ticker("TEST_USDT")
    assert normalized["volume_usd_24h"] == 1000000.0
    assert normalized["volume_usd_24h_measured"] is False
    assert normalized["volume_usd_24h_basis"] == "base_volume_close_proxy"
    safe, reasons = listing.check_safety(normalized, _deep_book(), _fresh_candles())
    assert safe is False
    assert any("24H-Volumen unbekannt" in reason for reason in reasons)


def test_listing_micro_quote_proxy_cannot_create_volume_confirmation():
    candles = _micro_crack_candles()
    assert listing.calculate_micro_crack_trigger(candles, {"ath": 130})["micro_trigger_ok"] is True
    proxy = [dict(row, volume_usd_measured=False, volume_usd_basis="base_volume_close_proxy")
             for row in candles]
    result = listing.calculate_micro_crack_trigger(proxy, {"ath": 130})
    assert result["micro_trigger_ok"] is False
    assert "micro_volume_missing_or_incomplete" in result["micro_warnings"]


def test_listing_exhaustion_quote_proxy_cannot_create_volume_decline_points(monkeypatch):
    candles = _hourly_listing_bars()
    monkeypatch.setattr(listing, "fetch_binance_candles", lambda *_a, **_kw: candles)
    for index, row in enumerate(candles):
        row["volume_usd"] = 1000000 if index < len(candles) // 2 else 10000
        row["volume_usd_measured"] = False
    _score, _details, result = listing.calculate_listing_exhaustion(candles, _fresh_ticker())
    assert result["vol_ratio"] is None


@pytest.mark.parametrize("clock", [None, True, "invalid", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_early_mover_final_context_cannot_promote_stale_or_unknown_provider_clock(clock):
    context = {"known": True, "data_status": "ok", "btc_24h": 1.0, "btc_7d": 2.0,
               "tailwind": True, "observed_at": datetime.now(timezone.utc).isoformat()}
    fields = api._extract_early_mover_fields({"btc_context": context})
    assert fields["btc_context_known"] is True
    context["observed_at"] = clock
    fields = api._extract_early_mover_fields({"btc_context": context})
    assert fields["btc_context_known"] is False
    assert api._early_mover_btc_allows_long(fields) is False


def _raw_early_coin_result(monkeypatch, **updates):
    coin = _volume_coin()
    coin["last_updated"] = datetime.now(timezone.utc).isoformat()
    coin.update(updates)
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [_btc(), coin])
    monkeypatch.setattr(api.req, "get", lambda *_a, **_kw: _TrendingResponse())
    result = api.fetch_early_movers(_prefetched_perps=_perp())
    return [row for row in result["coins"] if row.get("Symbol") == "TVOL"]


@pytest.mark.parametrize("field,raw", [
    ("current_price", True), ("current_price", float("nan")),
    ("market_cap", float("nan")), ("total_volume", float("nan")),
    ("price_change_percentage_24h", True), ("price_change_percentage_7d_in_currency", True),
    ("high_24h", True), ("low_24h", False),
])
def test_early_actual_producer_rejects_corrupt_raw_coin_evidence(monkeypatch, field, raw):
    assert _raw_early_coin_result(monkeypatch, **{field: raw}) == []


def test_early_actual_producer_measured_zero_week_does_not_select_alternate(monkeypatch):
    rows = _raw_early_coin_result(monkeypatch,
                                  price_change_percentage_7d_in_currency=0.0,
                                  price_change_percentage_7d=60.0)
    assert len(rows) == 1
    assert rows[0]["Change7d"] == 0.0


def test_early_actual_producer_genuine_coin_still_remains_candidate(monkeypatch):
    rows = _raw_early_coin_result(monkeypatch)
    assert len(rows) == 1
    assert rows[0]["Price"] == 1.0
    assert rows[0]["Change7d"] == 12.0


def test_early_optional_missing_returns_remain_null_not_measured_flat(monkeypatch):
    rows = _raw_early_coin_result(monkeypatch,
                                  price_change_percentage_1h_in_currency=None,
                                  price_change_percentage_14d_in_currency=None,
                                  price_change_percentage_30d_in_currency=None)
    assert len(rows) == 1
    assert rows[0]["Change1h"] is None
    assert rows[0]["Change14d"] is None
    assert rows[0]["Change30d"] is None
