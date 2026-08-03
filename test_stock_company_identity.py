import time
from pathlib import Path

import api
from modules import notify_telegram


ROOT = Path(__file__).resolve().parent


def test_stock_company_name_resolution_and_html_escaping(monkeypatch):
    monkeypatch.setitem(
        api._COMMON_STOCK_UNIVERSE_MEM,
        "names",
        {"AAPL": "Apple Inc."},
    )

    assert api._stock_company_name("AAPL") == "Apple Inc."

    rendered = api._format_stock_identity_html(
        "EVIL",
        {"company_name": "A < B Holdings"},
    )
    assert "<b>EVIL</b>" in rendered
    assert "A &lt; B Holdings" in rendered
    assert "A < B Holdings" not in rendered


def test_generic_name_is_only_accepted_in_explicit_stock_context(monkeypatch):
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "names", {})
    source = {"ticker": "AAPL", "name": "Apple Inc."}

    unchanged = api._attach_stock_company_name(source)
    enriched = api._attach_stock_company_name(source, allow_generic_name=True)

    assert "company_name" not in unchanged
    assert enriched["company_name"] == "Apple Inc."
    assert "company_name" not in source


def test_missing_reference_names_are_refresh_throttled(monkeypatch):
    now = time.time()
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "tickers", ["AAPL"])
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "loaded_at", now)
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "source", "memory")
    monkeypatch.setitem(api._COMMON_STOCK_UNIVERSE_MEM, "names", {})
    monkeypatch.setitem(
        api._COMMON_STOCK_UNIVERSE_MEM,
        "names_refresh_attempted_at",
        now,
    )

    def unexpected_network_call(*args, **kwargs):
        raise AssertionError("name refresh must be throttled")

    monkeypatch.setattr(api, "rate_limited_get", unexpected_network_call)

    tickers, source = api._load_common_stock_universe(require_names=True)

    assert tickers == {"AAPL"}
    assert source == "memory"


def test_telegram_stock_identity_does_not_relabel_crypto():
    stock_text = notify_telegram.format_alert_rows_for_telegram(
        [{
            "ticker": "AAPL",
            "company_name": "Apple Inc.",
            "direction": "LONG",
            "entry": 100,
            "stop": 95,
            "tp1": 110,
            "tp2": 120,
        }]
    )
    crypto_text = notify_telegram.format_alert_rows_for_telegram(
        [{
            "ticker": "BTC",
            "name": "Bitcoin",
            "direction": "LONG",
            "entry": 100,
            "stop": 95,
            "tp1": 110,
            "tp2": 120,
        }]
    )

    assert stock_text.startswith("AAPL - Apple Inc. LONG")
    assert crypto_text.startswith("BTC LONG")
    assert "Bitcoin" not in crypto_text


def test_stock_scanner_tables_use_shared_stock_identity_component():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "function StockIdentity" in source
    assert "function stockCompanyName" in source
    assert source.count("<StockIdentity") >= 10
    assert source.count("Ticker / Unternehmen") >= 5
