"""Real manual CoinGecko filter path: raw data must stay measured and fresh.

Run via tmp/offline_mail_fix_tests_20260925.py. This covers the eleven
registry views, not eleven independent trading strategies or real delivery.
"""
import copy
from datetime import datetime, timezone

import pytest

import api


def _coin(**updates):
    row = {
        "id": "audit-manual-coin", "symbol": "audit", "name": "Audit coin",
        "current_price": 1.08, "market_cap": 100_000_000,
        "total_volume": 20_000_000, "high_24h": 1.10, "low_24h": .95,
        "price_change_percentage_24h": 5.0,
        "price_change_percentage_7d_in_currency": 14.0,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    }
    row.update(updates)
    return row


def _run(monkeypatch, strategy, coin, btc=None, extra_rows=()):
    captured = []
    mails = []
    rows = list(copy.deepcopy(extra_rows)) + [copy.deepcopy(coin)]
    if btc is not None:
        rows.insert(0, copy.deepcopy(btc))
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: rows)
    monkeypatch.setattr(api, "_CG_MARKETS_STATUS", {"source": "live", "partial": False})
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *_a: None)
    monkeypatch.setattr(api, "save_partial_cache_file", lambda *_a, **_kw: None)
    monkeypatch.setattr(api, "_scan_control_point", lambda *_a, **_kw: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda _path, value, **_kw: captured.extend(value))
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", lambda *_args, **_kw: mails.append(_args))
    api._crypto_strategy_scan_wrapper(strategy)
    return [row for row in captured if row.get("ID") == coin.get("id")], mails


@pytest.mark.parametrize("field,raw", [
    ("current_price", True), ("current_price", "nan"), ("current_price", "inf"),
    ("market_cap", True), ("market_cap", -1), ("market_cap", "nan"),
    ("total_volume", True), ("total_volume", -1), ("total_volume", "nan"),
    ("high_24h", True), ("low_24h", False),
])
def test_manual_crypto_rejects_present_corrupt_provider_values(monkeypatch, field, raw):
    rows, _ = _run(monkeypatch, " Alle zeigen", _coin(**{field: raw}))
    assert rows == [], (field, raw, rows)


@pytest.mark.parametrize("raw", [
    None, True, "bad", "2020-01-01T00:00:00Z", "2099-01-01T00:00:00Z",
])
def test_manual_crypto_provider_timestamp_not_fetch_receipt(monkeypatch, raw):
    rows, _ = _run(monkeypatch, "Volume Surge", _coin(last_updated=raw))
    assert rows == []


@pytest.mark.parametrize("raw", [True, False])
def test_manual_daily_change_bool_is_not_a_measured_percent(monkeypatch, raw):
    rows, _ = _run(monkeypatch, " Alle zeigen", _coin(price_change_percentage_24h=raw))
    assert rows == []


@pytest.mark.parametrize("raw", [True, False])
def test_manual_week_bool_remains_unknown_context(monkeypatch, raw):
    rows, _ = _run(monkeypatch, " Alle zeigen", _coin(price_change_percentage_7d_in_currency=raw))
    assert len(rows) == 1
    assert rows[0]["Change7d"] is None
    assert rows[0]["prior_6d_geometric_average_pct"] is None


@pytest.mark.parametrize("bad", [None, "bad", []])
def test_corrupt_coin_row_does_not_abort_later_valid_manual_candidate(monkeypatch, bad):
    rows, _ = _run(monkeypatch, "Volume Surge", _coin(), extra_rows=[bad])
    assert len(rows) == 1


@pytest.mark.parametrize("updates", [
    {"high_24h": None}, {"low_24h": None},
    {"high_24h": .90, "low_24h": 1.10},
    {"current_price": 1.11}, {"current_price": .94},
])
def test_breakout_needs_actual_consistent_high_low_range(monkeypatch, updates):
    rows, _ = _run(monkeypatch, "Breakout Long", _coin(**updates))
    assert rows == []


def test_missing_range_remains_unknown_in_unfiltered_watch(monkeypatch):
    rows, _ = _run(monkeypatch, " Alle zeigen", _coin(high_24h=None, low_24h=None))
    assert len(rows) == 1
    assert rows[0]["Close_Position"] is None
    assert "close_position_unavailable" in rows[0]["risk_flags"]


def test_manual_micro_price_is_never_rounded_into_zero(monkeypatch):
    raw_price = .00000012345
    rows, _ = _run(monkeypatch, " Alle zeigen", _coin(
        current_price=raw_price, high_24h=raw_price * 1.02, low_24h=raw_price * .9,
    ))
    assert len(rows) == 1
    assert rows[0]["price"] == raw_price
    assert rows[0]["Preis"] == raw_price


def test_stale_btc_cannot_supply_seven_day_relative_bonus(monkeypatch):
    btc = _coin(id="bitcoin", symbol="btc", current_price=60_000,
                high_24h=61_000, low_24h=59_000,
                price_change_percentage_7d_in_currency=-20,
                last_updated="2020-01-01T00:00:00Z")
    rows, _ = _run(monkeypatch, "Volume Surge", _coin(), btc=btc)
    assert len(rows) == 1
    assert rows[0]["BtcRelative7d"] is None
    assert rows[0]["btc_relative_7d_status"] == "unavailable"


@pytest.mark.parametrize("strategy,updates", [
    (" Alle zeigen", {}),
    ("Volume Surge", {}),
    ("Bull Flag", {"price_change_percentage_24h": 0, "total_volume": 10_000_000}),
    ("Bear Flag", {"price_change_percentage_24h": 0, "price_change_percentage_7d_in_currency": -12,
                   "total_volume": 10_000_000}),
    ("Breakout Long", {}),
    ("Low Cap Rockets ", {}),
    ("Dip Buy", {"price_change_percentage_24h": -5, "total_volume": 10_000_000}),
    ("Reversal Hunter", {"price_change_percentage_7d_in_currency": -12}),
    ("Early Momentum", {}),
    ("Whale Watch ", {"total_volume": 30_000_000}),
    ("Accumulation ", {"price_change_percentage_24h": 0, "total_volume": 20_000_000}),
])
def test_all_eleven_registry_views_accept_genuine_positive_watch(monkeypatch, strategy, updates):
    rows, mails = _run(monkeypatch, strategy, _coin(**updates))
    assert len(rows) == 1, strategy
    row = rows[0]
    assert row["signal_quality"] == "observe"
    assert row["execution_trigger_ok"] is False
    assert row["alertable_crypto"] is False
    assert row["RVOL"] is None
    assert len(mails) == 1  # guard call only, not SMTP acceptance


def test_manual_change_filter_compares_raw_not_rounded_boundary(monkeypatch):
    rejected, _ = _run(monkeypatch, "Volume Surge", _coin(price_change_percentage_24h=2.9999))
    accepted, _ = _run(monkeypatch, "Volume Surge", _coin(price_change_percentage_24h=3.0))
    assert rejected == []
    assert len(accepted) == 1


def test_manual_turnover_is_percentage_over_ten_not_actual_rvol(monkeypatch):
    rows, _ = _run(monkeypatch, "Volume Surge", _coin(total_volume=15_000_000))
    assert len(rows) == 1
    assert rows[0]["VolMCapRatio"] == 15
    assert rows[0]["TurnoverIntensity"] == 1.5
    assert rows[0]["RVOL"] is None


def test_manual_prior_six_day_proxy_reverses_compounded_overlap():
    prior_daily = .75
    today = 2.0
    week = ((1 + prior_daily / 100) ** 6 * (1 + today / 100) - 1) * 100
    assert api._crypto_prior_six_day_average(week, today) == pytest.approx(prior_daily)
    assert api._crypto_prior_six_day_average(None, today) is None
    assert api._crypto_prior_six_day_average(-100, today) is None


@pytest.mark.parametrize("week,today", [(True, 2), (False, 2), (2, True), (2, False)])
def test_manual_prior_six_day_math_bool_is_not_a_return(week, today):
    assert api._crypto_prior_six_day_average(week, today) is None
