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


def test_final_revalidation_uses_exact_contract_and_latest_closed_bar(monkeypatch):
    seen = {}

    def fake_fetch(contract, venue, timeframe="5m", count=24):
        seen.update({"contract": contract, "venue": venue, "timeframe": timeframe})
        return [
            {"t": 900, "close": 99.0},
            {"t": 600, "close": 10.05},
            {"t": 300, "close": 10.0},
        ]

    monkeypatch.setattr(api, "_fetch_exchange_candles_any", fake_fetch)
    candidate = {
        "venue": "binance",
        "contract_symbol": "TESTUSDT",
        "price": 10.0,
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 11.5,
        "tp2": 13.0,
        "action": "LONG_TRIGGER",
    }

    result = api._revalidate_early_mover_mail_candidate(candidate, now_ts=1000)

    assert result["ok"] is True
    assert result["candidate"]["price"] == 10.05
    assert result["candidate"]["final_quote_source"] == "binance:TESTUSDT:5m_close"
    assert seen == {"contract": "TESTUSDT", "venue": "binance", "timeframe": "5m"}


def test_final_revalidation_rejects_identity_price_mismatch(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_exchange_candles_any",
        lambda *args, **kwargs: [
            {"t": 300, "close": 10.0},
            {"t": 600, "close": 20.0},
            {"t": 900, "close": 20.0},
        ],
    )
    candidate = {
        "venue": "mexc",
        "contract_symbol": "WRONGUSDT",
        "price": 10.0,
        "entry": 10.0,
        "stop": 9.0,
        "tp1": 21.0,
        "tp2": 23.0,
        "action": "LONG_TRIGGER",
    }

    result = api._revalidate_early_mover_mail_candidate(candidate, now_ts=1000)

    assert result == {"ok": False, "reason": "final_contract_price_mismatch"}
