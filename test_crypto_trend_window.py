"""CoinGecko watch-only six-day proxy math and threshold regressions."""
import pytest
import api


def test_six_day_proxy_removes_current_day_compound_factor():
    week = ((1.01 ** 6) * 1.20 - 1) * 100
    assert api._crypto_prior_six_day_average(week, 20) == pytest.approx(1.0)
    assert api._crypto_prior_six_day_average(20, 20) == pytest.approx(0.0)
    assert api._crypto_prior_six_day_average(0, 20) < 0


@pytest.mark.parametrize("week,today", [(None, 0), (0, None), (-100, 0), (0, -100), (float("inf"), 0), (0, float("nan"))])
def test_six_day_proxy_is_unavailable_for_missing_or_invalid_returns(week, today):
    assert api._crypto_prior_six_day_average(week, today) is None


@pytest.mark.parametrize("week,today,expected_rows", [(4, 3, 0), (((1.01 ** 7) - 1) * 100, 1, 1), (None, 1, 0)])
def test_crypto_flag_filter_uses_previous_six_days_not_seven_day_average(monkeypatch, week, today, expected_rows):
    coin = {"id": "test-coin", "symbol": "TEST", "name": "Test", "current_price": 100,
            "market_cap": 10_000_000, "total_volume": 1_000_000,
            "price_change_percentage_24h": today, "price_change_percentage_7d_in_currency": week,
            "high_24h": 101, "low_24h": 99}
    saved = {}
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_: [coin])
    monkeypatch.setattr(api, "_CG_MARKETS_STATUS", {})
    monkeypatch.setattr(api, "_is_excluded_crypto_asset", lambda *_: False)
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *_: "offline-crypto-cache")
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *_: None)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda *_, **__: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda path, rows: saved.update(rows=rows))
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *_: None)
    api._crypto_strategy_scan_wrapper("Bull Flag")
    assert len(saved["rows"]) == expected_rows
    if expected_rows:
        assert saved["rows"][0]["prior_6d_geometric_average_pct"] == pytest.approx(1.0)
        assert saved["rows"][0]["prior_trend_model"] == "derived_7d_over_24h_geometric6d_proxy"
        assert saved["rows"][0]["alertable_crypto"] is False
        assert saved["rows"][0]["execution_trigger_ok"] is False


@pytest.fixture
def generic_crypto_scan(monkeypatch):
    saved = {}
    monkeypatch.setattr(api, "_CG_MARKETS_STATUS", {})
    monkeypatch.setattr(api, "_is_excluded_crypto_asset", lambda *_: False)
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *_: "offline-crypto-context-cache")
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *_: None)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda *_, **__: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda path, rows: saved.update(rows=rows))
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *_: None)

    def run(week=20, today=0, btc=None, alternate_week=None):
        coin = {"id": "test-coin", "symbol": "TEST", "name": "Test", "current_price": 100,
                "market_cap": 10_000_000, "total_volume": 1_000_000,
                "price_change_percentage_24h": today, "price_change_percentage_7d_in_currency": week,
                "price_change_percentage_7d": alternate_week, "high_24h": 101, "low_24h": 99}
        # BTC context needs no valid market row of its own in this fake response.
        coins = [coin] + ([dict(btc, id="bitcoin")] if btc is not None else [])
        monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_: coins)
        api._crypto_strategy_scan_wrapper(" Alle zeigen")
        return saved["rows"]

    return run


def test_missing_btc_context_never_becomes_coin_alpha_score(generic_crypto_scan):
    missing = generic_crypto_scan()[0]
    zero = generic_crypto_scan(btc={"price_change_percentage_7d_in_currency": 0})[0]
    assert missing["BtcRelative7d"] is None
    assert missing["btc_change_7d_pct"] is None
    assert missing["btc_relative_7d_status"] == "unavailable"
    assert missing["context_missing_fields"] == ["btc_change_7d"]
    assert missing["score"] + 10 == zero["score"]
    assert zero["BtcRelative7d"] == 20
    assert zero["context_data_status"] == "ok"


def test_valid_zero_week_returns_do_not_select_alternate_fields(generic_crypto_scan):
    row = generic_crypto_scan(week=0, alternate_week=90, btc={
        "price_change_percentage_7d_in_currency": 0, "price_change_percentage_7d": -50,
    })[0]
    assert row["Change7d"] == row["btc_change_7d_pct"] == row["BtcRelative7d"] == 0
    assert row["btc_relative_7d_status"] == "available"
    assert row["context_missing_fields"] == []
    assert row["change_pct"] == 0


def test_missing_coin_week_is_unknown_without_fabricated_negative_alpha(generic_crypto_scan):
    unknown = generic_crypto_scan(week=None, btc={"price_change_percentage_7d_in_currency": 30})[0]
    zero = generic_crypto_scan(week=0, btc={"price_change_percentage_7d_in_currency": 30})[0]
    assert unknown["Change7d"] is None
    assert unknown["BtcRelative7d"] is None
    assert unknown["prior_6d_geometric_average_pct"] is None
    assert unknown["context_missing_fields"] == ["coin_change_7d"]
    assert unknown["score"] == zero["score"] + 12


@pytest.mark.parametrize("invalid", [None, "invalid", float("nan"), float("inf"), -100, -101])
def test_invalid_daily_change_never_becomes_a_generic_filter_hit(generic_crypto_scan, invalid):
    assert generic_crypto_scan(today=invalid) == []


@pytest.mark.parametrize("invalid", [None, "invalid", float("nan"), float("inf"), -100, -101])
def test_invalid_btc_week_does_not_create_relative_context(generic_crypto_scan, invalid):
    row = generic_crypto_scan(btc={"price_change_percentage_7d_in_currency": invalid})[0]
    assert row["BtcRelative7d"] is None
    assert row["btc_relative_7d_status"] == "unavailable"
    assert "btc_relative_7d_unavailable" in row["risk_flags"]


def test_missing_primary_week_uses_valid_alternate_without_zero_coercion(generic_crypto_scan):
    row = generic_crypto_scan(week=None, alternate_week=10, btc={
        "price_change_percentage_7d_in_currency": None, "price_change_percentage_7d": 10,
    })[0]
    assert row["Change7d"] == row["btc_change_7d_pct"] == 10
    assert row["BtcRelative7d"] == 0
    assert row["btc_relative_7d_status"] == "available"
