import time
from datetime import datetime, timedelta, timezone

import api
import modules.new_listing_scanner as new_listing_scanner
from modules.new_listing_scanner import (
    _attach_announcement_contracts,
    _clean_listing_base_symbol,
    _coverage_score_cap,
    _extract_listing_symbols_from_title,
    _first_exchange_candle_anchor,
    _is_tradeable_short_signal,
    _monitor_key,
    _parse_mexc_listing_announcements_html,
    _pump_base_symbol,
    add_to_monitoring,
    calculate_listing_exhaustion,
    calculate_micro_crack_trigger,
    check_safety,
    cleanup_monitoring,
    evaluate_signal_lifecycle,
    generate_short_signal,
)


def _fresh_ticker():
    return {
        "volume_usd_24h": 1_500_000,
        "bid": 96.9,
        "ask": 97.1,
        "timestamp": int(time.time()),
    }


def _fresh_candles():
    now = int(time.time())
    return [
        {"timestamp": now - 3600, "open": 95, "close": 96, "volume_usd": 50_000},
        {"timestamp": now, "open": 96, "close": 97, "volume_usd": 60_000},
    ]


def _deep_book():
    return {
        "bids": [(96.9, 200), (96.8, 200)],
        "asks": [(97.1, 200), (97.2, 200)],
    }


def _micro_crack_candles():
    now = int(time.time())
    start_ts = now - 25 * 300
    rows = []
    price = 80.0
    for i in range(18):
        rows.append({
            "timestamp": start_ts + i * 300,
            "open": price,
            "high": price * 1.018,
            "low": price * 0.995,
            "close": price * 1.015,
            "volume_usd": 100_000 + i * 2_000,
        })
        price *= 1.015
    high = 130.0
    rows.extend([
        {"timestamp": start_ts + 18 * 300, "open": price, "high": high, "low": 99, "close": 118, "volume_usd": 420_000},
        {"timestamp": start_ts + 19 * 300, "open": 118, "high": 119, "low": 115, "close": 116, "volume_usd": 360_000},
        {"timestamp": start_ts + 20 * 300, "open": 116, "high": 117, "low": 113, "close": 114, "volume_usd": 340_000},
        {"timestamp": start_ts + 21 * 300, "open": 114, "high": 116.5, "low": 112, "close": 115, "volume_usd": 280_000},
        {"timestamp": start_ts + 22 * 300, "open": 115, "high": 117, "low": 113, "close": 114.5, "volume_usd": 520_000},
    ])
    return rows


def _completed_listing_vrvp_bars(as_of):
    rows = []
    for index in range(36):
        base = (88.0 + (index % 3) * 0.25) if index < 18 else (104.0 + (index % 4) * 0.3)
        rows.append({
            "timestamp": int((as_of - timedelta(hours=37 - index)).timestamp()),
            "open": base,
            "high": base + 0.6,
            "low": base - 0.6,
            "close": base + (0.1 if index % 2 else -0.1),
            "volume": 100_000 + (index % 4) * 20_000,
        })
    return rows


def _listing_signal_pump_data(as_of, bars):
    return {
        "ath": 110.0,
        "current_price": 97.0,
        "pump_pct": 80.0,
        "from_ath_pct": 11.8,
        "momentum_recent": -1.0,
        "current_red_streak": 2,
        "avg_upper_wick_pct": 30.0,
        "micro_trigger_ok": True,
        "micro_score": 80,
        "micro_stop_loss": 101.0,
        "listing_source": "new_listing",
        "listing_age_hours": 24,
        "vrvp_as_of": as_of.isoformat(),
        "vrvp_bars": bars,
    }


def _with_causal_listing_vrvp(pump_data):
    as_of = datetime(2026, 1, 3, 12, 30, tzinfo=timezone.utc)
    return {
        **pump_data,
        "vrvp_as_of": as_of.isoformat(),
        "vrvp_bars": _completed_listing_vrvp_bars(as_of),
    }


def test_new_listing_flattener_preserves_projection_and_structure_provenance():
    setup = {
        "tp1_source": "ath_dump_projection",
        "tp2_source": "ath_dump_projection",
        "tp1_is_projection": True,
        "tp2_is_projection": True,
        "target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
        "barrier_gate": "STRUCTURAL_TP1_REQUIRED",
        "barrier_gate_active": True,
        "structure_status": "REJECT",
        "structure_reason": "confirmed_structural_tp1_required",
    }
    payload = {
        "watchlist": [{
            "symbol": "PROJUSDT",
            "exchange": "mexc",
            "signal": {
                "symbol": "PROJUSDT",
                "timing": "STRUCTURE_WAIT",
                "trade_category": "EXHAUSTION_WATCH",
                "listing_trade_ok": False,
                "entry": 1.0,
                "stop_loss": 1.1,
                "tp1": 0.85,
                "tp2": 0.7,
                "trade_setup": setup,
                "pump_data": {
                    "current_price": 1.0,
                    "funding_rate": 0.02,
                    "funding_rate_pct": 0.02,
                    "funding_rate_raw": 0.0002,
                    "funding_rate_unit": "fraction",
                    "funding_interval_hours": 8.0,
                },
            },
        }],
        "monitoring": [{
            "symbol": "LEGACYUSDT",
            "exchange": "binance",
            "price": 1.0,
            "trade_category": "EXHAUSTION_WATCH",
        }],
    }

    rows = api._flatten_new_listing_pipeline_results(payload)
    projection = next(row for row in rows if row["symbol"] == "PROJ")
    legacy_watch = next(row for row in rows if row["symbol"] == "LEGACY")

    assert projection["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"
    assert projection["tp1_source"] == "ath_dump_projection"
    assert projection["tp1_is_projection"] is True
    assert projection["barrier_gate"] == "STRUCTURAL_TP1_REQUIRED"
    assert projection["structure_status"] == "REJECT"
    assert projection["funding_rate_raw"] == 0.0002
    assert projection["funding_rate_pct"] == 0.02
    assert projection["funding_rate_unit"] == "fraction"

    normalized = api._normalize_crypto_short_signal(projection)
    assert normalized is not None
    assert normalized["trade_action"] == "SHORT_WATCH"
    assert normalized["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"
    assert normalized["tp1_source"] == "ath_dump_projection"
    assert normalized["tp1_is_projection"] is True
    assert normalized["structure_trade_block_reason"] == "active_structural_barrier_gate"

    # Legacy monitoring rows without target proof also remain fail-closed;
    # absence of provenance must never be upgraded to STRUCTURAL.
    assert legacy_watch["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"
    assert legacy_watch["tp1_source"] == "ath_dump_projection"
    assert legacy_watch["tp1_is_projection"] is True


def test_monitor_key_keeps_same_symbol_separate_by_exchange():
    assert _monitor_key("ABCUSDT", "mexc") != _monitor_key("ABCUSDT", "binance")


def test_listing_candle_anchor_requires_fresh_complete_uncapped_history():
    now = int(time.time())
    complete = [
        {"timestamp": now - ((48 - index) * 3600), "close": 1.0}
        for index in range(49)
    ]
    anchor = _first_exchange_candle_anchor(complete, requested_count=50)
    assert anchor is not None
    assert datetime.fromisoformat(anchor).timestamp() == complete[0]["timestamp"]

    capped = complete + [{"timestamp": now + 3600, "close": 1.0}]
    assert _first_exchange_candle_anchor(capped, requested_count=50) is None

    gapped = list(complete)
    gapped[20] = {"timestamp": gapped[19]["timestamp"] + 3 * 3600, "close": 1.0}
    assert _first_exchange_candle_anchor(gapped, requested_count=50) is None

    stale = [dict(row, timestamp=row["timestamp"] - 24 * 3600) for row in complete]
    assert _first_exchange_candle_anchor(stale, requested_count=50) is None


def test_listing_monitor_reanchors_to_exchange_timestamp(tmp_path, monkeypatch):
    path = tmp_path / "monitoring.json"
    monkeypatch.setattr(new_listing_scanner, "MONITORING_FILE", path)
    detected_ts = int(time.time()) - 1800
    corrected_ts = detected_ts - 900

    add_to_monitoring("ABCUSDT", "binance", detected_ts * 1000)
    first = new_listing_scanner.load_monitoring_list()["binance:ABCUSDT"]
    assert datetime.fromisoformat(first["listing_time"]).timestamp() == detected_ts

    add_to_monitoring("ABCUSDT", "binance", corrected_ts * 1000)
    updated = new_listing_scanner.load_monitoring_list()["binance:ABCUSDT"]
    assert datetime.fromisoformat(updated["listing_time"]).timestamp() == corrected_ts
    assert updated["listing_age_source_override"] == "exchange_timestamp"
    assert updated["listing_time_confirmed_at"]


def test_coverage_score_cap_is_bounded_and_has_no_threshold_jump():
    samples = [_coverage_score_cap(value / 1000.0) for value in range(1001)]
    assert samples == sorted(samples)
    assert samples[0] == 49
    assert samples[-1] == 100
    assert max(right - left for left, right in zip(samples, samples[1:])) <= 1


def test_listing_announcement_symbol_parser_keeps_crypto_and_blocks_stock_titles():
    assert _clean_listing_base_symbol("STARUSDT") == "STAR"
    assert _extract_listing_symbols_from_title("Binance Futures Will Launch STARUSDT Perpetual Contracts") == ["STAR"]
    assert _extract_listing_symbols_from_title("[Initial listing] Bitget to list Hooli (HOOLI) in the GameFi zone") == ["HOOLI"]
    assert _extract_listing_symbols_from_title("First in Market: MEXC to List AURASOLUSDT Futures") == ["AURASOL"]
    assert _extract_listing_symbols_from_title("Bitget Stock Futures will list NVDAUSDT shares") == []
    assert _extract_listing_symbols_from_title("MEXC to List JP225 Index Futures") == []


def test_mexc_announcement_parser_extracts_crypto_and_filters_stock_futures():
    html = '''
    <div class="SearchResultItem_titleWrapper__XkpJZ">
      <a title="First in Market: MEXC to List Hooli (HOOLI) USDT-M Futures on May 15, 2026, 11:20 (UTC)" href="/announcements/article/hooli"><h2>x</h2></a>
      <time dateTime="2026-05-15T10:55:21.000Z"><span>about 8 hours ago</span></time>
    </div>
    <div class="SearchResultItem_titleWrapper__XkpJZ">
      <a title="New Stock Futures Listings: CBRS USDT-M Futures to Launch on May 15 With 0-Fee Trading" href="/announcements/article/cbrs"><h2>x</h2></a>
      <time dateTime="2026-05-15T03:26:12.000Z"><span>about 15 hours ago</span></time>
    </div>
    '''

    rows = _parse_mexc_listing_announcements_html(html)

    assert len(rows) == 1
    assert rows[0]["source"] == "mexc_announcement"
    assert rows[0]["symbols"] == ["HOOLI"]
    assert rows[0]["url"].endswith("/announcements/article/hooli")


def test_listing_announcement_does_not_fallback_to_other_exchange_contract():
    announcements = [{
        "source": "bitget_announcement",
        "exchange": "bitget",
        "base": "UP",
        "symbols": ["UP"],
        "title": "Bitget to list Superform (UP)",
        "release_ms": int(time.time() * 1000),
    }]
    perps = [{
        "exchange": "mexc",
        "symbol": "UP_USDT",
        "base": "UP",
    }]

    backed_new = _attach_announcement_contracts(announcements, perps)

    assert backed_new == []
    assert announcements[0]["contract_confirmed"] is False
    assert announcements[0]["matched_contracts"] == []
    assert announcements[0]["cross_exchange_contracts"] == [{"exchange": "mexc", "symbol": "UP_USDT"}]
    assert announcements[0]["watch_reason"] == "contract_not_live_on_announcement_exchange"


def test_listing_announcement_creates_candidate_only_for_same_exchange_contract():
    announcements = [{
        "source": "bitget_announcement",
        "exchange": "bitget",
        "base": "UP",
        "symbols": ["UP"],
        "title": "Bitget to list Superform (UP)",
        "release_ms": int(time.time() * 1000),
    }]
    perps = [
        {"exchange": "mexc", "symbol": "UP_USDT", "base": "UP"},
        {"exchange": "bitget", "symbol": "UPUSDT", "base": "UP"},
    ]

    backed_new = _attach_announcement_contracts(announcements, perps)

    assert len(backed_new) == 1
    assert backed_new[0]["exchange"] == "bitget"
    assert backed_new[0]["symbol"] == "UPUSDT"
    assert backed_new[0]["announcement_exchange"] == "bitget"
    assert backed_new[0]["contract_confirmed"] is True
    assert announcements[0]["matched_contracts"] == [{"exchange": "bitget", "symbol": "UPUSDT"}]


def test_cleanup_expires_new_listing_by_exchange_listing_time_not_detection_time():
    now = datetime.now(timezone.utc)
    monitoring = {
        "binance:OLDUSDT": {
            "symbol": "OLDUSDT",
            "exchange": "binance",
            "source": "new_listing",
            "status": "monitoring",
            "detected_at": now.isoformat(),
            "listing_time": (now - timedelta(hours=96)).isoformat(),
        }
    }

    cleaned = cleanup_monitoring(monitoring)

    assert cleaned["binance:OLDUSDT"]["status"] == "expired"


def test_safety_requires_fresh_ticker_candles_and_orderbook():
    safe, warnings = check_safety(_fresh_ticker(), _deep_book(), _fresh_candles())
    assert safe is True
    assert any("OK" in warning for warning in warnings)

    safe, warnings = check_safety(_fresh_ticker(), None, _fresh_candles())
    assert safe is False
    assert any("Orderbook" in warning for warning in warnings)

    stale = _fresh_ticker()
    stale["timestamp"] = int(time.time()) - 3600
    safe, warnings = check_safety(stale, _deep_book(), _fresh_candles())
    assert safe is False
    assert any("Ticker stale" in warning for warning in warnings)


def test_listing_exhaustion_preserves_exchange_open_times_for_causal_vrvp(monkeypatch):
    as_of = datetime(2026, 1, 3, 12, 30, tzinfo=timezone.utc)
    candles = _completed_listing_vrvp_bars(as_of)
    for candle in candles:
        candle["volume_usd"] = candle["volume"]
    monkeypatch.setattr(new_listing_scanner, "fetch_binance_candles", lambda *_args, **_kwargs: [])

    _score, _details, pump_data = calculate_listing_exhaustion(
        candles,
        {},
        listing_age_hours=24,
        as_of=as_of,
    )

    assert pump_data["vrvp_timeframe"] == "1H"
    assert pump_data["vrvp_as_of"] == as_of.isoformat()
    assert [row["timestamp"] for row in pump_data["vrvp_bars"]] == [
        row["timestamp"] for row in candles
    ]
    assert [row["open_time"] for row in pump_data["vrvp_bars"]] == [
        row["timestamp"] for row in candles
    ]


def test_listing_vrvp_setup_is_invariant_to_open_and_future_extreme_bars():
    as_of = datetime(2026, 1, 3, 12, 30, tzinfo=timezone.utc)
    completed = _completed_listing_vrvp_bars(as_of)
    baseline = generate_short_signal(
        "CAUSALUSDT",
        _listing_signal_pump_data(as_of, completed),
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )
    open_extreme = {
        "timestamp": int(as_of.replace(minute=0, second=0, microsecond=0).timestamp()),
        "open": 2_000.0,
        "high": 5_000.0,
        "low": 1_000.0,
        "close": 4_500.0,
        "volume": 900_000_000_000,
    }
    future_extreme = {
        "timestamp": int((as_of + timedelta(hours=2)).timestamp()),
        "open": 0.03,
        "high": 9_000.0,
        "low": 0.01,
        "close": 8_000.0,
        "volume": 990_000_000_000,
    }
    augmented = generate_short_signal(
        "CAUSALUSDT",
        _listing_signal_pump_data(as_of, completed + [open_extreme, future_extreme]),
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert baseline is not None
    assert augmented is not None
    assert baseline["vrvp_applied"] is True
    assert augmented["vrvp_applied"] is True
    for key in (
        "entry", "stop_loss", "tp1", "tp2", "vrvp_poc", "vrvp_vah", "vrvp_val",
        "vrvp_atr", "vrvp_atr_source", "vrvp_completed_bar_count",
    ):
        assert augmented[key] == baseline[key]
    assert baseline["vrvp_completion_verified"] is True
    assert augmented["vrvp_completion_verified"] is True
    assert baseline["vrvp_completed_bar_count"] == len(completed)
    assert augmented["vrvp_completed_bar_count"] == len(completed)
    assert augmented["vrvp_raw_bar_count"] == len(completed) + 2
    assert augmented["vrvp_timeframe"] == "1H"


def test_listing_vrvp_is_not_applied_without_confirmable_bar_timestamps():
    as_of = datetime(2026, 1, 3, 12, 30, tzinfo=timezone.utc)
    unverified = [
        {key: value for key, value in row.items() if key != "timestamp"}
        for row in _completed_listing_vrvp_bars(as_of)
    ]

    signal = generate_short_signal(
        "UNVERIFIEDUSDT",
        _listing_signal_pump_data(as_of, unverified),
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal is not None
    assert signal["vrvp_applied"] is False
    assert signal["vrvp_completion_verified"] is False
    assert signal["vrvp_completed_bar_count"] == 0
    assert signal["vrvp_completion_reason"] == "unverified_vrvp_bar_timestamp"
    assert signal["vrvp_atr_source"] == "bounded_volatility_fallback"
    assert signal["trade_setup"]["tp1_is_projection"] is True
    assert signal["trade_setup"]["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"
    assert signal["tp1_is_projection"] is True
    assert signal["target_quality"] == signal["trade_setup"]["target_quality"]
    assert signal["barrier_gate"] == "STRUCTURAL_TP1_REQUIRED"
    assert signal["structure_status"] == "REJECT"
    assert signal["trade_signal"] != "JETZT_TRADEN"
    assert _is_tradeable_short_signal(signal) is False


def test_nested_structure_gate_blocks_native_short_tradeability():
    signal = {
        "direction": "SHORT",
        "timing_quality": 5,
        "grade": "S",
        "safety_ok": True,
        "confirmation_ok": True,
        "btc_context_ok": True,
        "micro_required": False,
        "continuation_risk": False,
        "tp1_missed": False,
        "tp2_missed": False,
        "rr_effective": 3.0,
        "risk_pct": 2.0,
        "listing_trade_ok": True,
        "trade_setup": {
            "tp1_is_projection": False,
            "barrier_gate": "BREAK_SUPPORT_REQUIRED",
            "barrier_gate_active": True,
            "structure_status": "WAIT_BREAK_RECLAIM",
        },
    }

    assert _is_tradeable_short_signal(signal) is False


def test_generate_short_signal_propagates_nested_structure_wait(monkeypatch):
    def gated_setup(setup, *_args, **_kwargs):
        return {
            **setup,
            "tp1": 90.0,
            "tp2": 82.0,
            "tp1_is_projection": False,
            "tp2_is_projection": True,
            "target_quality": "STRUCTURAL_FIRST_BARRIER",
            "barrier_gate": "BREAK_SUPPORT_REQUIRED",
            "barrier_gate_active": True,
            "structure_status": "WAIT_BREAK_RECLAIM",
            "structure_reason": "first_opposing_barrier_before_minimum_reward",
            "structure_decision": {
                "status": "WAIT_BREAK_RECLAIM",
                "barrier_gate": "BREAK_SUPPORT_REQUIRED",
            },
        }

    monkeypatch.setattr(new_listing_scanner, "apply_vrvp_to_trade_setup", gated_setup)
    signal = generate_short_signal(
        "GATEDUSDT",
        {
            "ath": 110.0,
            "current_price": 97.0,
            "pump_pct": 80.0,
            "from_ath_pct": 11.8,
            "momentum_recent": -1.0,
            "current_red_streak": 2,
            "avg_upper_wick_pct": 30.0,
            "micro_trigger_ok": True,
            "micro_score": 80,
            "micro_stop_loss": 101.0,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal is not None
    assert signal["barrier_gate"] == "BREAK_SUPPORT_REQUIRED"
    assert signal["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert signal["tp1_is_projection"] is False
    assert signal["target_quality"] == "STRUCTURAL_FIRST_BARRIER"
    assert signal["trade_setup"]["structure_status"] == signal["structure_status"]
    assert signal["trade_signal"] == "WARTEN"
    assert signal["trade_action"] == "WARTEN"
    assert signal["trade_category"] == "STRUCTURE_WAIT"
    assert signal["grade"] not in {"S", "A", "A+"}
    assert _is_tradeable_short_signal(signal) is False


def test_funding_telemetry_declares_raw_fraction_and_canonical_percent(monkeypatch):
    monkeypatch.setattr(new_listing_scanner, "fetch_binance_candles", lambda *_a, **_kw: [])
    as_of = datetime(2026, 1, 3, 12, 30, tzinfo=timezone.utc)
    candles = _completed_listing_vrvp_bars(as_of)[-12:]
    _score, _details, pump_data = calculate_listing_exhaustion(
        candles,
        {
            "funding_rate": 0.001,
            "funding_available": True,
            "funding_interval_hours": 4.0,
        },
        as_of=as_of,
    )

    assert pump_data["funding_rate_raw"] == 0.001
    assert pump_data["funding_rate_pct"] == 0.2
    assert pump_data["funding_rate"] == pump_data["funding_rate_pct"]
    assert pump_data["funding_rate_unit"] == "fraction"
    assert pump_data["funding_interval_hours"] == 8.0
    assert pump_data["funding_source_interval_hours"] == 4.0

    _score, _details, default_interval = calculate_listing_exhaustion(
        candles,
        {"funding_rate": 0.001, "funding_available": True},
        as_of=as_of,
    )
    assert default_interval["funding_rate_raw"] == 0.001
    assert default_interval["funding_rate_pct"] is None
    assert default_interval["funding_rate"] == default_interval["funding_rate_pct"]
    assert default_interval["funding_rate_unit"] == "fraction"
    assert default_interval["funding_interval_hours"] is None
    assert default_interval["funding_source_interval_hours"] is None
    assert default_interval["funding_available"] is False
    assert default_interval["funding_data_status"] == "missing_interval"


def test_pump_still_running_is_watch_not_tradeable_short():
    signal = generate_short_signal(
        "MOONUSDT",
        {
            "ath": 100,
            "current_price": 99,
            "pump_pct": 80,
            "from_ath_pct": 1,
            "momentum_recent": 2.0,
            "current_red_streak": 0,
            "avg_upper_wick_pct": 5,
        },
        exh_score=90,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["continuation_risk"] is True
    assert signal["confirmation_ok"] is False
    assert signal["timing_quality"] < 4
    assert _is_tradeable_short_signal(signal) is False


def test_confirmed_first_crack_with_rr_is_tradeable_short():
    signal = generate_short_signal(
        "CRACKUSDT",
        _with_causal_listing_vrvp({
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        }),
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["confirmation_ok"] is True
    assert signal["continuation_risk"] is False
    assert signal["rr_effective"] >= 1.5
    assert signal["grade"] in ("S", "A")
    assert signal["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(signal) is True


def test_new_listing_short_rejects_invalid_post_vrvp_geometry(monkeypatch):
    monkeypatch.setattr(
        new_listing_scanner,
        "apply_vrvp_to_trade_setup",
        lambda setup, *_args, **_kwargs: {
            **setup,
            "entry": 97,
            "stop": 101,
            "tp1": 90,
            "tp2": 92,
        },
    )

    signal = generate_short_signal(
        "BADLEVELUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal is None


def test_missing_listing_context_is_watch_only_even_if_crack_is_confirmed():
    signal = generate_short_signal(
        "UNKNOWNAGEUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["trade_category"] == "LISTING_INFO_MISSING"
    assert signal["listing_trade_ok"] is False
    assert "listing_info_missing" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_active_pump_detection_is_watch_only_even_with_crack():
    signal = generate_short_signal(
        "OLDPUMPUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "pump_detection",
            "listing_age_hours": None,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["trade_category"] == "EXHAUSTION_WATCH"
    assert signal["listing_trade_ok"] is False
    assert "active_pump_watch_only" in signal["risk_flags"]
    assert "bereits gecrackt" in signal["timing"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_btc_risk_on_waits_for_deeper_crack_before_short():
    signal = generate_short_signal(
        "BTCRISKONUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
            "btc_tailwind_risk": True,
            "btc_change_pct": 3.2,
            "coin_change_pct": 1.0,
            "btc_divergence": -2.2,
            "btc_short_context": "BTC_RISK_ON_WAIT_FOR_DEEPER_CRACK",
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["btc_context_ok"] is False
    assert "btc_risk_on_wait_for_deeper_crack" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_new_listing_age_window_required_for_short_mail_quality():
    too_early = generate_short_signal(
        "BABYUSDT",
        {
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 0.3,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )
    valid = generate_short_signal(
        "NEWUSDT",
        _with_causal_listing_vrvp({
            "ath": 100,
            "current_price": 97,
            "pump_pct": 80,
            "from_ath_pct": 3.0,
            "momentum_recent": -0.8,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 101,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        }),
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert too_early["trade_category"] == "NEW_LISTING_TOO_EARLY"
    assert too_early["listing_trade_ok"] is False
    assert "listing_too_early" in too_early["risk_flags"]
    assert _is_tradeable_short_signal(too_early) is False
    assert valid["trade_category"] == "NEW_LISTING_DUMP"
    assert valid["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(valid) is True


def test_early_crack_uses_local_rejection_stop_and_can_trade_below_old_score_gate():
    signal = generate_short_signal(
        "EARLYUSDT",
        _with_causal_listing_vrvp({
            "ath": 100,
            "current_price": 96,
            "pump_pct": 70,
            "from_ath_pct": 4.0,
            "momentum_recent": -0.2,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 30,
            "recent_rejection_high": 100,
            "recent_crack_depth_pct": 4.0,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
            "micro_trigger_ok": True,
            "micro_score": 75,
            "micro_stop_loss": 100,
            "listing_source": "new_listing",
            "listing_age_hours": 24,
        }),
        exh_score=50,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["setup_type"] == "early_crack"
    assert signal["stop_model"] == "micro_crack_stop"
    assert signal["stop_loss"] < signal["hard_stop_loss"]
    assert signal["grade"] == "A"
    assert signal["timing_quality"] == 4
    assert signal["signal_quality"] == "tradeable"
    assert _is_tradeable_short_signal(signal) is True


def test_early_crack_without_micro_trigger_stays_watchlist_only():
    signal = generate_short_signal(
        "GENIUSUSDT",
        {
            "ath": 0.5226,
            "current_price": 0.5006,
            "pump_pct": 20,
            "from_ath_pct": 4.2,
            "momentum_recent": -0.2,
            "current_red_streak": 1,
            "avg_upper_wick_pct": 25,
            "recent_rejection_high": 0.5226,
            "recent_crack_depth_pct": 4.2,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
            "micro_trigger_ok": False,
            "micro_score": 20,
        },
        exh_score=47,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert "micro_trigger_missing" in signal["risk_flags"]
    assert signal["timing_quality"] < 4
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_micro_crack_waits_when_first_vrvp_barrier_is_below_minimum_reward():
    pump_data = {
        "ath": 130,
        "current_price": _micro_crack_candles()[-1]["close"],
        "pump_pct": 80,
        "from_ath_pct": 9.0,
        "momentum_recent": 0.2,
        "current_red_streak": 0,
        "avg_upper_wick_pct": 5,
        "listing_source": "new_listing",
        "listing_age_hours": 24,
    }
    micro = calculate_micro_crack_trigger(_micro_crack_candles(), pump_data)
    pump_data.update(micro)
    pump_data = _with_causal_listing_vrvp(pump_data)
    signal = generate_short_signal(
        "MICROUSDT",
        pump_data,
        exh_score=35,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert micro["micro_trigger_ok"] is True
    assert signal["setup_type"] == "watch"
    assert signal["stop_model"] == "micro_crack_stop"
    assert signal["trade_setup"]["tp1_is_projection"] is False
    assert signal["trade_setup"]["nearest_barrier"]["distance_r"] < 1.5
    assert signal["barrier_gate"] == "BREAK_SUPPORT_REQUIRED"
    assert signal["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


def test_ultra_early_1m_crack_is_disabled():
    candles = _micro_crack_candles()[-12:]
    pump_data = {
        "ath": 130,
        "current_price": candles[-1]["close"],
        "pump_pct": 80,
        "from_ath_pct": 9.0,
        "listing_source": "new_listing",
        "listing_age_hours": 2,
    }

    micro = calculate_micro_crack_trigger(candles, pump_data, timeframe="1m")

    assert micro["micro_timeframe"] == "1m"
    assert micro["micro_trigger_ok"] is False
    assert "one_minute_execution_disabled" in micro["micro_warnings"]


def test_micro_crack_blocks_green_squeeze_without_first_crack():
    candles = _micro_crack_candles()
    last = candles[-1]
    last["open"] = last["close"] * 0.98
    last["high"] = last["close"] * 1.01
    last["low"] = last["open"] * 0.995
    last["close"] = last["high"] * 0.995

    micro = calculate_micro_crack_trigger(candles, {"ath": 135})

    assert micro["micro_trigger_ok"] is False
    assert "micro_still_squeezing" in micro["micro_warnings"] or "micro_too_early_no_crack" in micro["micro_warnings"]


def test_early_crack_blocks_after_tp1_or_without_structure():
    too_late = generate_short_signal(
        "LATEUSDT",
        {
            "ath": 100,
            "current_price": 79,
            "pump_pct": 70,
            "from_ath_pct": 21.0,
            "momentum_recent": -1.0,
            "current_red_streak": 2,
            "avg_upper_wick_pct": 35,
            "recent_rejection_high": 92,
            "recent_crack_depth_pct": 14.0,
            "prior_3_low_broken": True,
            "lower_high_confirmed": True,
        },
        exh_score=55,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )
    no_structure = generate_short_signal(
        "NOSTRUCTUSDT",
        {
            "ath": 100,
            "current_price": 96,
            "pump_pct": 70,
            "from_ath_pct": 4.0,
            "momentum_recent": 0.8,
            "current_red_streak": 0,
            "avg_upper_wick_pct": 5,
            "recent_rejection_high": 96.5,
            "recent_crack_depth_pct": 0.5,
            "prior_3_low_broken": False,
            "lower_high_confirmed": False,
        },
        exh_score=55,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert too_late["signal_quality"] == "watch_or_blocked"
    assert too_late["tp1_missed"] is True
    assert no_structure["signal_quality"] == "watch_or_blocked"
    assert "crack_structure_weak" in no_structure["risk_flags"]


def test_low_rr_confirmed_crack_stays_watchlist_only():
    signal = generate_short_signal(
        "NORRUSDT",
        {
            "ath": 100,
            "current_price": 90,
            "pump_pct": 80,
            "from_ath_pct": 10,
            "momentum_recent": -1.5,
            "current_red_streak": 2,
            "avg_upper_wick_pct": 30,
        },
        exh_score=85,
        exh_details=[],
        safety_ok=True,
        safety_warnings=[],
    )

    assert signal["rr_effective"] < 1.5
    assert "rr_too_low" in signal["risk_flags"]
    assert signal["signal_quality"] == "watch_or_blocked"
    assert _is_tradeable_short_signal(signal) is False


# ── H-15 Audit-Fix: Signale sind kein One-Shot mehr ──

def test_signal_lifecycle_invalidates_short_when_price_breaches_stop():
    mon = {
        "status": "signal",
        "signal_direction": "SHORT",
        "signal_stop_loss": 105.0,
        "signal_at": datetime.now(timezone.utc).isoformat(),
    }

    status, reason = evaluate_signal_lifecycle(dict(mon), 106.0)
    assert status == "invalidated"
    assert "stop_breached" in reason

    # Exakt am Stop = Breach (SHORT: Preis >= Stop)
    status, _ = evaluate_signal_lifecycle(dict(mon), 105.0)
    assert status == "invalidated"

    # Unter dem Stop bleibt das Signal aktiv
    status, reason = evaluate_signal_lifecycle(dict(mon), 99.0)
    assert status == "signal"
    assert reason is None


def test_signal_lifecycle_invalidates_long_when_price_breaches_stop():
    mon = {
        "status": "signal",
        "signal_direction": "LONG",
        "signal_stop_loss": 95.0,
        "signal_at": datetime.now(timezone.utc).isoformat(),
    }

    status, reason = evaluate_signal_lifecycle(mon, 94.0)
    assert status == "invalidated"
    assert "stop_breached" in reason


def test_signal_lifecycle_expires_after_24h():
    mon = {
        "status": "signal",
        "signal_direction": "SHORT",
        "signal_stop_loss": 105.0,
        "signal_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
    }

    status, reason = evaluate_signal_lifecycle(mon, 99.0)
    assert status == "expired"
    assert "24" in reason


def test_signal_lifecycle_ignores_non_signal_entries():
    mon = {"status": "monitoring"}
    status, reason = evaluate_signal_lifecycle(mon, 50.0)
    assert status == "monitoring"
    assert reason is None


def test_pump_base_symbol_dedupes_cross_exchange_symbols():
    # M-Pumps Audit-Fix: MEXC "ABC_USDT" und Binance/Bitget "ABCUSDT" sind derselbe Coin
    assert _pump_base_symbol("ABC_USDT") == _pump_base_symbol("ABCUSDT") == "ABC"
    assert _pump_base_symbol("btc_usdt") == "BTC"
