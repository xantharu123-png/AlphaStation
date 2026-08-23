import time

import api
from modules.new_listing_scanner import calculate_micro_crack_trigger
from modules.strategies import CRYPTO_STRATEGIES


def _execution_bars(count=36, *, age_bars=1, last_close=10.1, last_volume=3000):
    now = int(time.time())
    start = now - (count + age_bars) * 300
    rows = []
    price = 9.5
    for idx in range(count):
        close = price + 0.01
        rows.append({
            "timestamp": start + idx * 300,
            "open": price,
            "high": close + 0.02,
            "low": price - 0.02,
            "close": close,
            "volume": 1000,
            "volume_usd": 1000,
        })
        price = close
    rows[-1].update({
        "open": last_close - 0.08,
        "high": last_close + 0.02,
        "low": last_close - 0.1,
        "close": last_close,
        "volume": last_volume,
        "volume_usd": last_volume,
    })
    return rows


def _early_row(action="LONG_TRIGGER"):
    return {
        "Symbol": "TEST",
        "Price": 10.1,
        "current_price": 10.1,
        "PerpChartSymbol": "TESTUSDT",
        "PerpChartExchange": "binance",
        "trade_action": action,
        "entry": 10.0,
        "stop_loss": 9.5,
        "tp1": 11.0,
        "tp2": 12.0,
        "setup_score": 85,
        "score": 85,
        "risk_level": "LOW",
        "trade_setup": {"entry": 10.0, "stop_loss": 9.5, "tp1": 11.0, "tp2": 12.0},
    }


def test_early_mover_rejects_stale_execution_candle():
    bars = _execution_bars(age_bars=8)
    scored = api._score_early_mover_trigger_bars(_early_row(), bars, "5m", {})

    assert scored["ok"] is False
    assert scored["reason"] == "stale_5m_execution_candle"
    assert scored["execution_data_age_seconds"] > 600


def test_early_mover_missing_volume_history_cannot_confirm_entry():
    bars = _execution_bars(age_bars=1)
    for bar in bars[:-1]:
        bar["volume"] = 0
        bar["volume_usd"] = 0

    scored = api._score_early_mover_trigger_bars(_early_row(), bars, "5m", {})

    assert scored["ok"] is False
    assert scored["reason"] == "missing_5m_volume_baseline"


def test_early_mover_trigger_cache_is_bound_to_trade_plan(monkeypatch):
    api._EARLY_MOVER_TRIGGER_CACHE.clear()
    calls = []

    monkeypatch.setattr(api, "fetch_candles_for", lambda *args, **kwargs: [])

    def fake_score(row, bars, timeframe, profile):
        calls.append(row["entry"])
        return {"ok": False, "reason": "no_fresh_5m_trigger", "timeframe": "5m", "execution_score": 0}

    monkeypatch.setattr(api, "_score_early_mover_trigger_bars", fake_score)
    first = _early_row()
    second = _early_row()
    second["entry"] = 10.2
    second["trade_setup"] = {**second["trade_setup"], "entry": 10.2}

    api._verify_early_mover_intraday_trigger(first)
    api._verify_early_mover_intraday_trigger(second)

    assert calls == [10.0, 10.2]


def test_wait_for_retest_needs_actual_retest_hold():
    row = _early_row("WAIT_FOR_RETEST")
    api._apply_early_mover_signal_state(row, {
        "ok": True,
        "reason": "adaptive_5m_breakout",
        "timeframe": "5m",
        "execution_score": 88,
        "matched": ["breakout"],
        "last_close": 10.1,
        "volume_ratio": 2.4,
    })

    assert row["trade_signal"] == "WARTEN"
    assert row["entry_status"] == "WAIT_FOR_RETEST"
    assert row["alertable_crypto"] is False
    assert row["RVOL"] == 2.4
    assert row["volume_model"] == "exchange_5m_vs_median"


def test_forming_micro_crack_candle_is_not_a_short_trigger():
    rows = _execution_bars(count=24, age_bars=1, last_close=9.4, last_volume=5000)
    forming = dict(rows[-1])
    forming.update({
        "timestamp": int(time.time()) - 60,
        "open": 9.5,
        "high": 9.55,
        "low": 8.7,
        "close": 8.8,
        "volume_usd": 50_000,
    })
    rows.append(forming)

    result = calculate_micro_crack_trigger(rows, {"ath": 12.0}, timeframe="5m")

    assert result["micro_dropped_open_candle"] is True
    assert result["micro_current_price"] != 8.8


def test_contract_multiplier_is_explicit_not_a_price_mismatch():
    assert api._crypto_contract_price_multiplier("PEPE", "1000PEPEUSDT") == 1000
    assert api._crypto_contract_price_multiplier("BTC", "BTCUSDT") == 1


def test_short_watch_never_normalizes_to_trade_now():
    row = {
        "symbol": "WATCH",
        "trade_action": "SHORT_WATCH",
        "trade_category": "EXHAUSTION_WATCH",
        "micro_trigger_ok": True,
        "safety_ok": True,
        "listing_trade_ok": True,
        "exhaustion_score": 75,
        "micro_score": 70,
        "price": 1.0,
    }

    normalized = api._normalize_crypto_short_signal(row)

    assert normalized is not None
    assert normalized["trade_action"] == "SHORT_WATCH"
    assert normalized["trade_signal"] == "WARTEN"


def test_crypto_snapshot_strategies_do_not_call_turnover_rvol():
    for config in CRYPTO_STRATEGIES.values():
        assert "RVOL" not in config.get("filters", {})


def test_live_crypto_signals_require_timestamped_fresh_execution_data():
    long_row = {
        "Symbol": "LONG",
        "trade_signal": "JETZT_TRADEN",
        "execution_trigger_ok": True,
        "explosion_score": 90,
        "entry_score": 90,
        "Price": 1.0,
    }
    short_row = {
        "symbol": "SHORT",
        "trade_action": "SHORT_NOW",
        "trade_category": "NEW_LISTING_DUMP",
        "micro_trigger_ok": True,
        "safety_ok": True,
        "listing_trade_ok": True,
        "exhaustion_score": 90,
        "price": 1.0,
    }

    normalized_long = api._normalize_crypto_long_signal(long_row)
    normalized_short = api._normalize_crypto_short_signal(short_row)

    assert normalized_long["trade_action"] == "LONG_ARMED"
    assert normalized_long["trade_signal"] == "WARTEN"
    assert normalized_short["trade_action"] == "SHORT_WATCH"
    assert normalized_short["trade_signal"] == "WARTEN"


def test_crypto_trade_normalizers_fail_closed_on_every_structure_blocker():
    blockers = (
        {"barrier_gate": "BREAK_RECLAIM_REQUIRED", "barrier_gate_active": True},
        {"structure_status": "WAIT_BREAK_RECLAIM"},
        {"structure_status": "REJECT"},
        {"target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER", "tp1_is_projection": True},
        {"trade_setup": {"target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER", "tp1_is_projection": True}},
    )
    for index, blocker in enumerate(blockers):
        long_row = {
            "Symbol": f"LONG{index}",
            "trade_signal": "JETZT_TRADEN",
            "execution_trigger_ok": True,
            "execution_data_age_seconds": 60,
            "explosion_score": 90,
            "entry_score": 90,
            "Price": 1.0,
            **blocker,
        }
        short_row = {
            "symbol": f"SHORT{index}",
            "trade_action": "SHORT_NOW",
            "trade_category": "NEW_LISTING_DUMP",
            "micro_trigger_ok": True,
            "micro_data_age_seconds": 60,
            "safety_ok": True,
            "listing_trade_ok": True,
            "exhaustion_score": 90,
            "price": 1.0,
            **blocker,
        }

        normalized_long = api._normalize_crypto_long_signal(long_row)
        normalized_short = api._normalize_crypto_short_signal(short_row)

        assert normalized_long["trade_action"] == "LONG_ARMED"
        assert normalized_long["trade_signal"] == "WARTEN"
        assert normalized_long["alertable_crypto"] is False
        assert normalized_long["structure_trade_block_reason"]
        assert normalized_short["trade_action"] == "SHORT_WATCH"
        assert normalized_short["trade_signal"] == "WARTEN"
        assert normalized_short["alertable_crypto"] is False
        assert normalized_short["structure_trade_block_reason"]


def test_crypto_merge_rechecks_structure_after_normalization(monkeypatch):
    blocked = {
        "Symbol": "CACHE",
        "symbol": "CACHE",
        "direction": "LONG",
        "trade_action": "JETZT_LONG",
        "trade_signal": "JETZT_TRADEN",
        "decision": "JETZT_LONG",
        "barrier_gate": "BREAK_RECLAIM_REQUIRED",
        "structure_status": "WAIT_BREAK_RECLAIM",
        "target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
        "tp1_is_projection": True,
    }
    monkeypatch.setattr(api, "_normalize_crypto_long_signal", lambda row: dict(row))

    rows = api._merge_crypto_trade_signals([blocked], [])

    assert rows[0]["trade_action"] == "LONG_ARMED"
    assert rows[0]["trade_signal"] == "WARTEN"
    assert rows[0]["alertable_crypto"] is False
    assert rows[0]["structure_trade_block_reason"] == "active_structural_barrier_gate"


def test_final_crypto_structure_acceptance_masks_stale_nested_wait_state():
    row = {
        "Symbol": "FINAL",
        "trade_signal": "JETZT_TRADEN",
        "execution_trigger_ok": True,
        "execution_data_age_seconds": 60,
        "explosion_score": 90,
        "entry_score": 90,
        "Price": 1.0,
        "barrier_gate": None,
        "structure_status": "ACCEPT",
        "target_quality": "STRUCTURAL_FIRST_BARRIER",
        "tp1_is_projection": False,
        "trade_setup": {
            "barrier_gate": "BREAK_RECLAIM_REQUIRED",
            "barrier_gate_active": True,
            "structure_status": "REJECT",
            "target_quality": "PROJECTION_ONLY_NO_CONFIRMED_BARRIER",
            "tp1_is_projection": True,
        },
    }

    normalized = api._normalize_crypto_long_signal(row)

    assert normalized["trade_action"] == "JETZT_LONG"
    assert normalized["trade_signal"] == "JETZT_TRADEN"
    assert "structure_trade_block_reason" not in normalized


def test_cached_live_crypto_row_without_candle_age_is_downgraded():
    rows = [{
        "Symbol": "STALECACHE",
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "JETZT_LONG",
        "execution_trigger_ok": True,
    }]

    normalized = api._downgrade_expired_crypto_triggers(rows, cache_age=30)

    assert normalized[0]["trade_signal"] == "WARTEN"
    assert normalized[0]["trade_action"] == "WAIT_FOR_BREAKOUT"
    assert normalized[0]["execution_trigger_ok"] is False
    assert normalized[0]["trigger_expired"] is True
