import time

import api


def _bars(count, start=9.6, step=0.002, volume=1000, last=None, interval=300):
    rows = []
    price = start
    first_timestamp = int(time.time()) - (count + 1) * interval
    for idx in range(count):
        open_ = price
        close = price + step
        high = max(open_, close) + 0.015
        low = min(open_, close) - 0.015
        rows.append({
            "timestamp": first_timestamp + idx * interval,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })
        price = close
    if last:
        rows[-1].update(last)
    return rows


def _candidate(price=9.95, change=4.0):
    return {
        "exchange": "bybit",
        "BestExchange": "Bybit",
        "contract": "TESTUSDT",
        "Symbol": "TEST",
        "Price": price,
        "price": price,
        "Change24h": change,
        "turnover_24h_usd": 60_000_000,
        "high_24h": 10.4,
        "low_24h": 9.1,
        "funding_rate": 0.01,
        "funding_rate_unit": "percent",
        "funding_interval_hours": 8,
        "spread_pct": 0.02,
        "HasPerp": True,
        "isCrypto": True,
    }


def _btc_context(change, btc_24h=0.1):
    return {
        "btc_24h": btc_24h,
        "btc_7d": 1.0,
        "coin_24h": change,
        "alpha_24h": change - btc_24h,
        "tailwind": True,
        "allows_long": True,
        "known": True,
        "data_status": "ok",
    }


def test_crypto_explosion_armed_is_not_market_buy(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change, 0.2))
    bars5 = _bars(90, start=9.48, step=0.004, volume=1000, last={
        "open": 9.93,
        "high": 9.97,
        "low": 9.90,
        "close": 9.95,
        "volume": 1200,
    })
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900, last={
        "open": 9.92,
        "high": 9.98,
        "low": 9.88,
        "close": 9.95,
        "volume": 3600,
    })
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)

    scored = api._score_crypto_explosion_candidate(_candidate(), bars5, bars15, bars4h)

    assert scored is not None
    assert scored["trade_signal"] == "EXPLOSION_ARMED"
    assert scored["trade_decision"] == "WAIT_FOR_BREAK_RECLAIM"
    assert scored["barrier_gate_active"] is True
    assert scored["tp2"] > scored["tp1"] > scored["entry"] > scored["stop"]
    assert scored["risk_reward"] >= 1.45


def test_crypto_explosion_near_observed_high_waits_for_first_barrier_reclaim(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    bars5 = _bars(90, start=9.50, step=0.004, volume=1000, last={
        "open": 10.00,
        "high": 10.14,
        "low": 9.98,
        "close": 10.12,
        "volume": 3200,
    })
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900, last={
        "open": 9.95,
        "high": 10.02,
        "low": 9.91,
        "close": 9.98,
        "volume": 3000,
    })
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)

    scored = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8.0), bars5, bars15, bars4h)

    assert scored is not None
    assert scored["trade_signal"] == "EXPLOSION_ARMED"
    assert scored["execution_trigger_ok"] is True
    assert scored["entry_score"] >= 80
    assert scored["tp2"] > scored["tp1"] > scored["entry"] > scored["stop"]
    assert scored["alertable_crypto"] is False


def test_crypto_explosion_projection_only_tp1_cannot_become_trade_now(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    bars5 = _bars(90, start=9.50, step=0.004, volume=1000, last={
        "open": 10.00,
        "high": 10.14,
        "low": 9.98,
        "close": 10.12,
        "volume": 3200,
    })
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900, last={
        "open": 9.95,
        "high": 10.02,
        "low": 9.91,
        "close": 9.98,
        "volume": 3000,
    })
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)
    candidate = _candidate(price=10.12, change=8.0)
    candidate["high_24h"] = 10.0

    scored = api._score_crypto_explosion_candidate(candidate, bars5, bars15, bars4h)

    assert scored is not None
    assert scored["execution_trigger_ok"] is True
    assert scored["trade_signal"] == "EXPLOSION_ARMED"
    assert scored["target_quality"].startswith("PROJECTION_ONLY")
    assert scored["tp1_is_projection"] is True
    assert scored["alertable_crypto"] is False
    assert api._alert_trade_plan_ok(scored, require_native_levels=False) is False


def test_crypto_explosion_funding_is_explicitly_percent_qualified():
    row = api._ce_normalized_row(
        exchange="bybit",
        contract="TESTUSDT",
        price=10.0,
        change_24h=4.0,
        turnover_usd=60_000_000,
        funding_rate=0.1,
    )

    assert row["funding_rate_pct"] == 0.1
    assert row["funding_rate_raw"] == 0.1
    assert row["funding_rate_unit"] == "percent"


def test_crypto_explosion_rejects_late_parabolic_move(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change, 0.0))
    bars5 = _bars(90, start=8.4, step=0.018, volume=1200, last={
        "open": 10.4,
        "high": 10.8,
        "low": 10.2,
        "close": 10.65,
        "volume": 3500,
    })
    bars15 = _bars(60, start=8.0, step=0.035, volume=3000, interval=900)
    bars4h = _bars(60, start=6.8, step=0.045, volume=5000, interval=14400)
    for row in bars4h[-5:]:
        row["close"] = row["open"] * 1.04
        row["high"] = row["close"] * 1.01

    scored = api._score_crypto_explosion_candidate(_candidate(price=10.65, change=42.0), bars5, bars15, bars4h)

    assert scored is None


def test_crypto_explosion_ignores_forming_breakout_candle(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    bars5 = _bars(90, start=9.50, step=0.004, volume=1000, last={
        "open": 9.92,
        "high": 9.98,
        "low": 9.90,
        "close": 9.94,
        "volume": 1100,
    })
    bars5.append({
        "timestamp": int(time.time()) - 60,
        "open": 9.94,
        "high": 10.20,
        "low": 9.93,
        "close": 10.16,
        "volume": 5000,
    })
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900)
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)

    scored = api._score_crypto_explosion_candidate(_candidate(), bars5, bars15, bars4h)

    assert scored is None or scored["trade_signal"] != "JETZT_TRADEN"


def test_crypto_explosion_rejects_stale_5m_confirmation(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda *args: _btc_context(4.0))
    bars5 = _bars(90, start=9.50, step=0.004, volume=1000, last={
        "open": 10.00,
        "high": 10.14,
        "low": 9.98,
        "close": 10.12,
        "volume": 3200,
    })
    for bar in bars5:
        bar["timestamp"] -= 24 * 3600
    bars15 = _bars(60, start=9.42, step=0.009, volume=3000, interval=900)
    bars4h = _bars(60, start=9.4, step=0.006, volume=5000, interval=14400)

    assert api._score_crypto_explosion_candidate(_candidate(price=10.12), bars5, bars15, bars4h) is None
