"""Stored result reads never wait for reference-provider calls (offline only)."""
import json
import time

import pytest

import api


@pytest.fixture
def references(monkeypatch, tmp_path):
    cache = tmp_path / "universe.json"
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache))
    monkeypatch.setattr(api, "POLYGON_KEY", "OFFLINE-REFERENCE-KEY")
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {
        "loaded_at": 0, "tickers": None, "names": {}, "source": "not_loaded",
        "adr_tickers": [], "names_refresh_attempted_at": 0,
    })
    monkeypatch.setattr(api, "_ORB_REFERENCE_CACHE", {})
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})

    def forbidden(*args, **kwargs):
        pytest.fail("A display read must never call the reference provider")

    monkeypatch.setattr(api, "rate_limited_get", forbidden)
    return cache


@pytest.mark.parametrize("names", [{}, {"AAPL": "Apple Inc."}])
def test_cold_memory_display_uses_saved_universe_without_name_refresh(references, names):
    stamp = time.time() - 600
    references.write_text(json.dumps({
        "cached_at": stamp, "tickers": ["AAPL"], "names": names,
    }), encoding="utf-8")
    tickers, source = api._load_common_stock_universe(require_names=True, cache_only=True)
    assert tickers == {"AAPL"}
    assert source == "file_cache"
    assert api._COMMON_STOCK_UNIVERSE_MEM["loaded_at"] == stamp
    assert api._COMMON_STOCK_UNIVERSE_MEM["names"] == names
    assert api._COMMON_STOCK_UNIVERSE_MEM["names_refresh_attempted_at"] == 0


def test_stale_reference_display_does_not_rejuvenate_evidence(references):
    stamp = time.time() - 3 * 86400
    references.write_text(json.dumps({
        "cached_at": stamp, "tickers": ["AAPL"], "names": {"AAPL": "Apple Inc."},
    }), encoding="utf-8")
    tickers, source = api._load_common_stock_universe(require_names=True, cache_only=True)
    assert tickers == {"AAPL"}
    assert source == "stale_file_cache"
    assert api._COMMON_STOCK_UNIVERSE_MEM["loaded_at"] == stamp
    # Display admission does not promote stale evidence for diagnostic/mail use.
    assert api._load_common_stock_universe_cached()[0] is None


def test_empty_result_never_loads_stammdaten(references, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("No reference work is needed for an empty result list")
    monkeypatch.setattr(api, "_load_common_stock_universe", forbidden)
    assert api._decorate_scan_results([], "strategy_scan", 10) == []


def test_decoration_retains_company_name_and_excludes_non_stock_names(references):
    references.write_text(json.dumps({
        "cached_at": time.time(), "tickers": ["AAPL", "QAETF"],
        "names": {"AAPL": "Apple Inc.", "QAETF": "Synthetic ETF Fund"},
    }), encoding="utf-8")
    rows = api._decorate_scan_results([
        {"Ticker": "AAPL", "score": 80}, {"Ticker": "QAETF", "score": 99},
    ], "strategy_scan", 10)
    assert [row["Ticker"] for row in rows] == ["AAPL"]
    assert rows[0]["company_name"] == "Apple Inc."


def test_unknown_instrument_evidence_is_503_not_a_successful_zero(references):
    with pytest.raises(api.HTTPException) as failure:
        api._decorate_scan_results([{"Ticker": "AAPL", "score": 80}], "strategy_scan", 10)
    assert failure.value.status_code == 503
    assert failure.value.detail["code"] == "stock_reference_cache_unavailable"


@pytest.mark.parametrize("reason", ["reference_unavailable", "reference unavailable HTTP 503"])
def test_cached_reference_failure_is_not_relabelled_as_non_stock(references, reason):
    api._ORB_REFERENCE_CACHE["AAPL"] = (False, reason)
    with pytest.raises(api.HTTPException) as failure:
        api._decorate_scan_results([{"Ticker": "AAPL", "score": 80}], "strategy_scan", 10)
    assert failure.value.status_code == 503


def test_cached_per_ticker_admission_preserves_known_rows_without_provider(references):
    api._ORB_REFERENCE_CACHE.update({"AAPL": (True, "CS"), "QAETF": (False, "type=ETF")})
    rows = api._decorate_scan_results([
        {"Ticker": "AAPL", "score": 80}, {"Ticker": "QAETF", "score": 99},
    ], "strategy_scan", 10)
    assert [row["Ticker"] for row in rows] == ["AAPL"]


def test_default_scanner_reference_refresh_remains_enabled(references, monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def json(self):
            return {"results": [{"ticker": "AAPL", "type": "CS", "market": "stocks", "name": "Apple Inc."}]}
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: calls.append(args) or Response())
    assert api._load_common_stock_universe(require_names=True)[0] == {"AAPL"}
    assert calls


def test_crypto_decoration_never_loads_stock_reference(references, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Crypto rows have no stock-reference dependency")
    monkeypatch.setattr(api, "_load_common_stock_universe", forbidden)
    rows = api._decorate_scan_results([{"ticker": "BTCUSDT", "score": 80}], "crypto_explosion", 10)
    assert rows[0]["ticker"] == "BTCUSDT"
