"""Actual BTC-divergence watch producer: fresh/raw evidence before bias gates."""
from datetime import datetime, timezone

import pytest

import api


def _coin(**updates):
    row = {"id": "context-audit", "symbol": "ctxt", "name": "Context coin",
           "current_price": 1.0, "market_cap": 100_000_000, "total_volume": 20_000_000,
           "price_change_percentage_24h": 10.0, "price_change_percentage_7d_in_currency": 20.0,
           "last_updated": datetime.now(timezone.utc).isoformat()}
    row.update(updates)
    return row


def _btc(**updates):
    row = _coin(id="bitcoin", symbol="btc", name="Bitcoin", current_price=60_000,
                price_change_percentage_24h=0.0, price_change_percentage_7d_in_currency=0.0)
    row.update(updates)
    return row


def _run(monkeypatch, coin=None, btc=None, extra_rows=()):
    coin = _coin() if coin is None else coin
    btc = _btc() if btc is None else btc
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: list(extra_rows) + [btc, coin])
    monkeypatch.setattr(api, "_CG_MARKETS_STATUS", {"source": "fresh_live", "partial": False})
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    monkeypatch.setattr(api, "_scan_control_point", lambda *_a, **_kw: None)
    return api._build_crypto_btc_divergence_results()


@pytest.mark.parametrize("raw", [None, True, "bad", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_btc_divergence_requires_real_fresh_reference_clock(monkeypatch, raw):
    assert _run(monkeypatch, btc=_btc(last_updated=raw)) == []


@pytest.mark.parametrize("raw", [None, True, "bad", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z"])
def test_btc_divergence_requires_real_fresh_coin_clock(monkeypatch, raw):
    assert _run(monkeypatch, coin=_coin(last_updated=raw)) == []


@pytest.mark.parametrize("field", ["price_change_percentage_24h", "price_change_percentage_7d_in_currency"])
def test_btc_divergence_reference_bool_is_not_a_market_return(monkeypatch, field):
    assert _run(monkeypatch, btc=_btc(**{field: True})) == []


@pytest.mark.parametrize("field,raw", [
    ("current_price", True), ("current_price", "nan"), ("current_price", "inf"),
    ("market_cap", "nan"), ("market_cap", "inf"),
    ("total_volume", "nan"), ("total_volume", "inf"),
    ("price_change_percentage_24h", True), ("price_change_percentage_7d_in_currency", True),
])
def test_btc_divergence_never_promotes_corrupt_measured_coin(monkeypatch, field, raw):
    assert _run(monkeypatch, coin=_coin(**{field: raw})) == []


@pytest.mark.parametrize("bad", [None, "bad", []])
def test_btc_divergence_bad_row_does_not_destroy_valid_watch(monkeypatch, bad):
    rows = _run(monkeypatch, extra_rows=[bad])
    assert len(rows) == 1


def test_btc_divergence_short_alpha_threshold_uses_raw_difference(monkeypatch):
    btc = _btc(price_change_percentage_24h=-2.0, price_change_percentage_7d_in_currency=7.0)
    rejected = _run(monkeypatch, btc=btc, coin=_coin(
        price_change_percentage_24h=3.9999, price_change_percentage_7d_in_currency=18.0))
    accepted = _run(monkeypatch, btc=btc, coin=_coin(
        price_change_percentage_24h=4.0, price_change_percentage_7d_in_currency=18.0))
    assert all(not row["signal"].startswith("SHORT-WATCH") for row in rejected)
    assert len(accepted) == 1 and accepted[0]["signal"].startswith("SHORT-WATCH")


def test_btc_divergence_turnover_threshold_uses_raw_percentage(monkeypatch):
    btc = _btc(price_change_percentage_24h=2.0, price_change_percentage_7d_in_currency=5.0)
    rejected = _run(monkeypatch, btc=btc, coin=_coin(
        price_change_percentage_24h=6.0, price_change_percentage_7d_in_currency=13.0,
        total_volume=120_004_000))
    accepted = _run(monkeypatch, btc=btc, coin=_coin(
        price_change_percentage_24h=6.0, price_change_percentage_7d_in_currency=13.0,
        total_volume=120_000_000))
    assert all(not row["signal"].startswith("LONG-WATCH") for row in rejected)
    assert len(accepted) == 1 and accepted[0]["signal"].startswith("LONG-WATCH")


def test_btc_divergence_genuine_zero_reference_remains_watch_not_mail(monkeypatch):
    rows = _run(monkeypatch)
    assert len(rows) == 1
    row = rows[0]
    assert row["btc_24h"] == row["btc_7d"] == 0
    assert row["signal"].startswith("SHORT-WATCH")
    assert row["context_only"] is True
    assert row["execution_trigger_ok"] is False
    assert row["trade_signal"] == "BEOBACHTEN"
    assert row["score_is_probability"] is False
