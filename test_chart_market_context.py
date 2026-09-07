"""Chart routing and venue classification must agree without network access."""

import pytest

from modules import data_fetchers as df


MARKET_CASES = [
    ("AAPL", "AAPL", "polygon", "equity", True),
    ("aapl", "aapl", "polygon", "equity", True),
    ("BRK.B", "BRK.B", "polygon", "equity", True),
    ("X:BTCUSD", "X:BTCUSD", "polygon", "crypto", False),
    ("x:ethusd", "x:ethusd", "polygon", "crypto", False),
    ("BTC-USD", "BTC-USD", "yahoo", "crypto", False),
    ("eth-eur", "eth-eur", "yahoo", "crypto", False),
    ("SOL-GBP", "SOL-GBP", "yahoo", "crypto", False),
    ("EURUSD=X", "EURUSD=X", "yahoo", "forex", False),
    ("eurusd=x", "eurusd=x", "yahoo", "forex", False),
    ("GC=F", "GC=F", "yahoo", "futures", False),
    ("gc=f", "gc=f", "yahoo", "futures", False),
] + [
    (f"TEST{suffix}", f"TEST{suffix}", "yahoo", "equity", False)
    for suffix in (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK", ".de")
] + [
    (alias.lower(), f"{alias}-USD", "yahoo", "crypto", False)
    for alias in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC")
]


@pytest.mark.parametrize("ticker,route_ticker,provider,asset_class,us_session", MARKET_CASES)
def test_chart_market_context_matches_existing_routes(
    ticker, route_ticker, provider, asset_class, us_session
):
    assert df.chart_market_context(ticker) == {
        "ticker": route_ticker,
        "provider": provider,
        "asset_class": asset_class,
        "us_equity_session": us_session,
    }


@pytest.mark.parametrize("ticker,route_ticker,provider,asset_class,us_session", MARKET_CASES)
def test_chart_fetcher_uses_same_market_context_without_changing_output(
    monkeypatch, ticker, route_ticker, provider, asset_class, us_session
):
    expected = [{"time": 123, "open": 10, "high": 12, "low": 9, "close": 11, "volume": 5}]
    calls = []

    def fake_yahoo(symbol, timeframe):
        calls.append(("yahoo", symbol, timeframe))
        return expected

    def fake_polygon(symbol, api_key, timeframe):
        assert api_key == "test-key"
        calls.append(("polygon", symbol, timeframe))
        return expected

    monkeypatch.setattr(df, "_fetch_ohlcv_yahoo", fake_yahoo)
    monkeypatch.setattr(df, "_fetch_ohlcv_polygon", fake_polygon)

    result = df.fetch_ohlcv_for_chart(ticker, "test-key", timeframe="1W", bars=17)

    assert calls == [(provider, route_ticker, "1W")]
    assert result is expected


def test_chart_market_context_is_not_shared_mutable_state():
    context = df.chart_market_context("AAPL")
    context["us_equity_session"] = False
    context["ticker"] = "X:BTCUSD"

    assert df.chart_market_context("AAPL")["us_equity_session"] is True
    assert df.chart_market_context("AAPL")["ticker"] == "AAPL"
