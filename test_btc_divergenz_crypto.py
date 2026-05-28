import api


def test_btc_divergence_uses_crypto_coins_not_crypto_equities(monkeypatch):
    coins = [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "current_price": 100000,
            "market_cap": 2_000_000_000_000,
            "total_volume": 40_000_000_000,
            "price_change_percentage_24h": -1.0,
            "price_change_percentage_7d_in_currency": 0.2,
        },
        {
            "id": "aztec",
            "symbol": "aztec",
            "name": "Aztec",
            "current_price": 0.0217,
            "market_cap": 60_000_000,
            "total_volume": 8_000_000,
            "price_change_percentage_24h": 11.2,
            "price_change_percentage_7d_in_currency": 26.0,
        },
    ]
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=4: coins)
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {
        "AZTEC": {
            "best_contract_symbol": "AZTECUSDT",
            "best_chart_exchange": "binance",
            "best_exchange": "Binance",
            "volume24_usdt": 8_000_000,
        }
    })

    rows = api._build_crypto_btc_divergence_results()

    assert [row["symbol"] for row in rows] == ["AZTEC"]
    assert rows[0]["isCrypto"] is True
    assert rows[0]["contract"] == "AZTECUSDT"
    assert rows[0]["signal"].startswith("SHORT-WATCH")
    assert rows[0]["trade_action"] == "WAIT_FOR_SHORT_TRIGGER"


def test_btc_divergence_does_not_include_static_crypto_equity_list(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=4: [
        {
            "id": "bitcoin",
            "symbol": "btc",
            "name": "Bitcoin",
            "current_price": 100000,
            "market_cap": 2_000_000_000_000,
            "total_volume": 40_000_000_000,
            "price_change_percentage_24h": 0,
            "price_change_percentage_7d_in_currency": 0,
        }
    ])
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})

    rows = api._build_crypto_btc_divergence_results()

    assert rows == []
