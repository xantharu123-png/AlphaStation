import api


class _TrendingResponse:
    status_code = 200

    def json(self):
        return {"coins": []}


def _btc(change_24h=1.0, change_7d=2.0):
    return {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 60_000,
        "market_cap": 1_000_000_000_000,
        "total_volume": 10_000_000_000,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": change_7d,
    }


def _volume_coin(symbol="tvol", coin_id="test-volume", change_24h=4.0):
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": "Test Volume",
        "current_price": 1.0,
        "market_cap_rank": 321,
        "market_cap": 20_000_000,
        "total_volume": 12_000_000,
        "price_change_percentage_1h_in_currency": 1.5,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": 12.0,
        "price_change_percentage_14d_in_currency": 8.0,
        "price_change_percentage_30d_in_currency": 10.0,
        "high_24h": 1.05,
        "low_24h": 0.90,
    }


def test_early_mover_volume_spike_builds_conditional_long_setup(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), _volume_coin()])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps={})
    row = next(c for c in result["coins"] if c["Symbol"] == "TVOL")

    assert row["direction"] == "LONG"
    assert row["trade_action"] == "LONG_TRIGGER"
    assert row["entry"] > row["stop_loss"]
    assert row["tp1"] > row["entry"]
    assert row["tp2"] > row["tp1"]
    assert row["risk_reward"] >= 1.5
    assert row["btc_context"]["tailwind"] is True


def test_early_mover_filters_stables_wrapped_and_liquid_staking(monkeypatch):
    usde = _volume_coin(symbol="usde", coin_id="ethena-usde")
    usde["name"] = "Ethena USDe Stablecoin"
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), usde])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps={})

    assert all(c["Symbol"] != "USDE" for c in result["coins"])
    assert result["stats"]["excluded_assets"] >= 1


def test_early_mover_btc_headwind_blocks_active_long_trigger(monkeypatch):
    weak_btc = _btc(change_24h=-4.0, change_7d=-8.0)
    coin = _volume_coin(change_24h=3.5)
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [weak_btc, coin])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps={})
    row = next(c for c in result["coins"] if c["Symbol"] == "TVOL")

    assert row["trade_action"] == "WAIT_FOR_BTC_CONFIRMATION"
    assert "btc_headwind" in row["risk_flags"]
    assert row["btc_context"]["tailwind"] is False


def test_early_mover_perp_positioning_marks_snapshot_only(monkeypatch):
    coin = _volume_coin(symbol="whale", coin_id="whale-test", change_24h=2.0)
    coin["market_cap"] = 80_000_000
    coin["total_volume"] = 3_000_000
    coin["price_change_percentage_7d_in_currency"] = 2.0
    perps = {
        "WHALE": {
            "funding_rate": 0.0001,
            "oi_ratio": 2.0,
            "oi_usdt": 5_000_000,
            "volume24_usdt": 2_000_000,
            "best_exchange": "MEXC",
            "best_contract_symbol": "WHALE_USDT",
            "best_chart_exchange": "mexc",
            "exchanges": ["MEXC", "Bitget"],
            "oi_change_pct": None,
        }
    }
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), coin])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps=perps)
    row = next(c for c in result["coins"] if c["Symbol"] == "WHALE")

    assert "Perp Positioning" in row["sources"]
    assert row["oi_snapshot_only"] is True
    assert "oi_snapshot_only" in row["risk_flags"]
    assert row["Rank"] == 321
    assert row["PerpChartSymbol"] == "WHALE_USDT"
    assert row["PerpChartExchange"] == "mexc"


def test_early_mover_nested_coin_rows_receive_quality_payload(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), _volume_coin()])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())
    result = api.fetch_early_movers(_prefetched_perps={})

    decorated = api._decorate_early_mover_results([result], cache_age_seconds=15)
    row = decorated[0]["coins"][0]

    assert "_quality" in row
    assert row["_quality"]["why_in"]
    assert row["trade_health"]["metrics"]["entry"] == row["entry"]
