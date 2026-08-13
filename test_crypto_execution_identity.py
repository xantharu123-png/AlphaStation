import api


def test_price_validated_perp_accepts_scaled_1000_contract():
    perp_data = {
        "1000PEPE": {
            "best_contract_symbol": "1000PEPEUSDT",
            "best_last_price": 9.5,
            "volume24_usdt": 5_000_000,
        }
    }

    key, info, gap = api._select_price_validated_perp("PEPE", 0.0095, perp_data)

    assert key == "1000PEPE"
    assert info["best_contract_symbol"] == "1000PEPEUSDT"
    assert gap == 0.0


def test_price_validated_perp_rejects_same_name_wrong_price():
    perp_data = {
        "COIN": {
            "best_contract_symbol": "COINUSDT",
            "best_last_price": 100.0,
            "volume24_usdt": 5_000_000,
        }
    }

    key, info, gap = api._select_price_validated_perp("COIN", 1.0, perp_data)

    assert key is None
    assert info == {}
    assert gap is None


def test_live_orderbook_quote_selects_long_ask_and_short_bid(monkeypatch):
    monkeypatch.setattr(api.time, "time", lambda: 1000.0)
    monkeypatch.setattr(
        api,
        "_fetch_crypto_orderbook_any",
        lambda *args, **kwargs: {
            "bids": [[9.99, 10_000]],
            "asks": [[10.01, 10_000]],
        },
    )

    long_quote = api._fetch_crypto_executable_quote("TESTUSDT", "binance", "LONG")
    short_quote = api._fetch_crypto_executable_quote("TESTUSDT", "binance", "SHORT")

    assert long_quote["ok"] is True
    assert long_quote["price"] == 10.01
    assert long_quote["price_mode"] == "ask"
    assert short_quote["ok"] is True
    assert short_quote["price"] == 9.99
    assert short_quote["price_mode"] == "bid"
    assert long_quote["observed_ts"] == 1000.0
    assert long_quote["source"] == "binance:TESTUSDT:live_orderbook_top"


def test_live_orderbook_quote_fails_closed_on_slow_receipt(monkeypatch):
    times = iter([1000.0, 1011.0])
    monkeypatch.setattr(api.time, "time", lambda: next(times))
    monkeypatch.setattr(
        api,
        "_fetch_crypto_orderbook_any",
        lambda *args, **kwargs: {
            "bids": [[9.99, 10_000]],
            "asks": [[10.01, 10_000]],
        },
    )

    result = api._fetch_crypto_executable_quote("TESTUSDT", "binance", "LONG")

    assert result == {"ok": False, "reason": "final_executable_quote_stale"}


def _continuous_path(*, touched_stop=False, touched_tp1=False):
    return [
        {
            "timestamp": 900,
            "open": 10.0,
            "high": 11.1 if touched_tp1 else 10.5,
            "low": 8.9 if touched_stop else 9.5,
            "close": 10.2,
        },
        {
            "timestamp": 960,
            "open": 10.2,
            "high": 10.6,
            "low": 9.6,
            "close": 10.4,
        },
    ]


def _quote(*, observed_ts=1000, bid=9.99, ask=10.0, mode="ask"):
    price = bid if mode == "bid" else ask
    return {
        "ok": True,
        "price": price,
        "bid": bid,
        "ask": ask,
        "spread_bps": 10.0,
        "depth_10bps_min_usd": 10_000,
        "depth_25bps_min_usd": 30_000,
        "depth_50bps_min_usd": 60_000,
        "observed_ts": observed_ts,
        "observed_at": "1970-01-01T00:16:40+00:00",
        "source": "binance:TESTUSDT:live_orderbook_top",
        "price_mode": mode,
        "price_session": "CRYPTO_24_7",
    }


def test_final_crypto_gate_rejects_stale_executable_quote(monkeypatch):
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *args, **kwargs: _continuous_path())
    monkeypatch.setattr(
        api,
        "_fetch_crypto_executable_quote",
        lambda *args, **kwargs: _quote(observed_ts=980),
    )
    candidate = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "scan_price_observed_at": 900,
        "scan_price_source": "exchange_trigger:last_close",
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.0,
        "tp2": 12.0,
    }

    result = api._revalidate_crypto_trade_mail_candidate(
        candidate,
        direction="LONG",
        now_ts=1000,
    )

    assert result == {"ok": False, "reason": "final_executable_quote_stale"}


def test_final_crypto_gate_fails_closed_without_original_observation(monkeypatch):
    calls = []
    monkeypatch.setattr(
        api,
        "_fetch_crypto_executable_quote",
        lambda *args, **kwargs: calls.append("quote"),
    )
    candidate = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.0,
        "tp2": 12.0,
    }

    result = api._revalidate_crypto_trade_mail_candidate(
        candidate,
        direction="LONG",
        now_ts=1000,
    )

    assert result == {
        "ok": False,
        "reason": "final_source_observation_timestamp_missing",
    }
    assert calls == []


def test_final_crypto_gate_blocks_stop_touch_even_after_retrace(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_exchange_candles_any",
        lambda *args, **kwargs: _continuous_path(touched_stop=True),
    )
    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", lambda *args, **kwargs: _quote())
    candidate = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "scan_price_observed_at": 900,
        "scan_price_source": "exchange_trigger:last_close",
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.0,
        "tp2": 12.0,
    }

    result = api._revalidate_crypto_trade_mail_candidate(
        candidate,
        direction="LONG",
        now_ts=1000,
    )

    assert result == {"ok": False, "reason": "final_stop_touched_since_scan"}


def test_final_crypto_gate_blocks_short_stop_touch_after_retrace(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_exchange_candles_any",
        lambda *args, **kwargs: [
            {"timestamp": 900, "open": 10.0, "high": 11.1, "low": 9.5, "close": 10.2},
            {"timestamp": 960, "open": 10.2, "high": 10.6, "low": 9.6, "close": 10.0},
        ],
    )
    monkeypatch.setattr(
        api,
        "_fetch_crypto_executable_quote",
        lambda *args, **kwargs: _quote(mode="bid"),
    )

    result = api._revalidate_crypto_trade_mail_candidate(
        {
            "venue": "binance",
            "contract_symbol": "TESTUSDT",
            "scan_price_observed_at": 900,
            "scan_price_source": "binance:closed_5m_micro",
            "entry": 10.0,
            "stop": 11.0,
            "tp1": 9.0,
            "tp2": 8.0,
        },
        direction="SHORT",
        now_ts=1000,
    )

    assert result == {"ok": False, "reason": "final_stop_touched_since_scan"}


def test_final_crypto_gate_blocks_tp1_touch_even_after_retrace(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_exchange_candles_any",
        lambda *args, **kwargs: _continuous_path(touched_tp1=True),
    )
    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", lambda *args, **kwargs: _quote())
    candidate = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "scan_price_observed_at": 900,
        "scan_price_source": "exchange_trigger:last_close",
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.0,
        "tp2": 12.0,
    }

    result = api._revalidate_crypto_trade_mail_candidate(
        candidate,
        direction="LONG",
        now_ts=1000,
    )

    assert result == {"ok": False, "reason": "final_tp1_touched_since_scan"}


def test_final_crypto_gate_uses_ask_for_long_and_bid_for_short(monkeypatch):
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *args, **kwargs: _continuous_path())

    def fake_quote(contract, venue, direction):
        return _quote(mode="bid" if direction == "SHORT" else "ask")

    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", fake_quote)
    common = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "scan_price_observed_at": 900,
        "scan_price_source": "exchange_trigger:last_close",
        "entry": 10.0,
    }

    long_result = api._revalidate_crypto_trade_mail_candidate(
        {**common, "stop": 9.0, "tp1": 11.0, "tp2": 12.0},
        direction="LONG",
        now_ts=1000,
    )
    short_result = api._revalidate_crypto_trade_mail_candidate(
        {**common, "stop": 11.0, "tp1": 9.0, "tp2": 8.0},
        direction="SHORT",
        now_ts=1000,
    )

    assert long_result["ok"] is True
    assert long_result["candidate"]["price"] == 10.0
    assert long_result["candidate"]["price_mode"] == "ask"
    assert short_result["ok"] is True
    assert short_result["candidate"]["price"] == 9.99
    assert short_result["candidate"]["price_mode"] == "bid"
    assert long_result["candidate"]["fill_evidence_verified"] is False
    assert short_result["candidate"]["fill_evidence_verified"] is False


def test_final_crypto_gate_quotes_before_fetching_watermarked_path(monkeypatch):
    calls = []

    def fake_quote(*args, **kwargs):
        calls.append("quote")
        return _quote()

    def fake_path(contract, venue, timeframe="1m", count=3):
        calls.append(("path", contract, venue, timeframe, count))
        return _continuous_path()

    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", fake_quote)
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fake_path)
    result = api._revalidate_crypto_trade_mail_candidate(
        {
            "venue": "binance",
            "contract_symbol": "TESTUSDT",
            "scan_price_observed_at": 900,
            "scan_price_source": "exchange_trigger:last_close",
            "entry": 10.0,
            "stop": 9.0,
            "tp1": 11.0,
            "tp2": 12.0,
        },
        direction="LONG",
        now_ts=1000,
    )

    assert result["ok"] is True
    assert calls[0] == "quote"
    assert calls[1][:4] == ("path", "TESTUSDT", "binance", "1m")


def test_new_listing_final_gate_reprices_short_to_live_bid(monkeypatch):
    monkeypatch.setattr(
        api,
        "_revalidate_crypto_trade_mail_candidate",
        lambda candidate, direction, now_ts=None: {
            "ok": True,
            "candidate": {
                **candidate,
                "price": 10.0,
                "current_price": 10.0,
                "price_mode": "bid",
                "price_source": "binance:TESTUSDT:live_orderbook_top",
                "price_observed_at": "2026-08-13T12:00:00+00:00",
                "fill_evidence_verified": False,
                "final_quote_spread_bps": 8.0,
                "final_quote_depth_50bps_min_usd": 25_000.0,
            },
        },
    )
    alert = {
        "symbol": "TEST",
        "exchange": "binance",
        "setup": "new_listing_dump",
        "entry": 10.2,
        "stop": 11.0,
        "tp1": 9.0,
        "tp2": 8.0,
        "cooldown_key": "new_listing_TEST",
        "source_row": {
            "symbol": "TESTUSDT",
            "contract_symbol": "TESTUSDT",
            "venue": "binance",
            "scan_price_observed_at": 1000,
            "scan_price_source": "binance:closed_5m_micro",
        },
    }

    result = api._revalidate_new_listing_mail_candidate(alert, now_ts=1010)

    assert result["ok"] is True
    candidate = result["candidate"]
    assert candidate["entry"] == 10.0
    assert candidate["planned_entry"] == 10.2
    assert candidate["source_row"]["entry"] == 10.0
    assert candidate["source_row"]["price_mode"] == "bid"
    assert candidate["source_row"]["fill_evidence_verified"] is False


def test_early_mover_final_gate_reprices_long_to_live_ask(monkeypatch):
    monkeypatch.setattr(
        api,
        "_revalidate_crypto_trade_mail_candidate",
        lambda candidate, direction, now_ts=None: {
            "ok": True,
            "candidate": {
                **candidate,
                "price": 10.05,
                "current_price": 10.05,
                "price_mode": "ask",
                "price_source": "binance:TESTUSDT:live_orderbook_top",
                "price_observed_at": "2026-08-13T12:00:00+00:00",
                "fill_evidence_verified": False,
                "final_quote_spread_bps": 8.0,
                "final_quote_depth_10bps_min_usd": 10_000.0,
                "final_quote_depth_25bps_min_usd": 30_000.0,
                "final_quote_depth_50bps_min_usd": 60_000.0,
            },
        },
    )
    candidate = {
        "symbol": "TEST",
        "key": "early_TEST",
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "price": 10.0,
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.5,
        "tp2": 13.0,
        "action": "LONG_TRIGGER",
    }

    result = api._revalidate_early_mover_mail_candidate(candidate, now_ts=1010)

    assert result["ok"] is True
    validated = result["candidate"]
    assert validated["entry"] == 10.05
    assert validated["planned_entry"] == 10.0
    assert validated["price_mode"] == "ask"
    assert validated["fill_evidence_verified"] is False
