"""Public API errors must never expose provider URLs or credential queries."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

import api


_CANARY = "https://provider.invalid/v1/data?apiKey=SUPERSECRET&token=ALSOSECRET"


def _assert_redacted(call, expected_code: str, capsys) -> None:
    with pytest.raises(HTTPException) as exc_info:
        call()
    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == {"error": expected_code}
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured


def test_ticker_detail_provider_exception_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )
    _assert_redacted(
        lambda: api.get_ticker_detail("AAPL"), "ticker_detail_failed", capsys
    )


def test_stock_chart_provider_exception_is_redacted(monkeypatch, capsys):
    api._CHART_CACHE.clear()
    monkeypatch.setattr(
        api,
        "fetch_ohlcv_for_chart",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )
    _assert_redacted(
        lambda: api.get_chart_data("AAPL", "1D", "ema", None),
        "chart_data_failed",
        capsys,
    )


def test_crypto_chart_provider_exception_is_redacted(monkeypatch, capsys):
    api._CRYPTO_CHART_CACHE.clear()
    monkeypatch.setattr(
        api,
        "fetch_daily_candles_crypto",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )
    _assert_redacted(
        lambda: api.get_crypto_chart("bitcoin", 30), "crypto_chart_failed", capsys
    )


def test_exchange_chart_provider_exception_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(
        api,
        "_fetch_exchange_candles_any",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )
    _assert_redacted(
        lambda: api.get_exchange_chart("BTCUSDT", "bybit", "1h", 100),
        "exchange_chart_failed",
        capsys,
    )


def test_orb_atr_model_never_returns_provider_exception(monkeypatch, capsys):
    api._ORB_ATR_CACHE.clear()
    monkeypatch.setattr(api, "POLYGON_KEY", "configured")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )

    _, model = api._fetch_orb_atr_pct(
        "AAPL", api.datetime.now(api.timezone.utc), 2.5
    )

    assert model == "prev_day_range_error"
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured


def test_market_headline_error_is_stable_and_log_is_redacted(monkeypatch, capsys):
    monkeypatch.setattr(api, "POLYGON_KEY", "configured")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )

    headlines, error = api._fetch_polygon_market_headlines()

    assert headlines == []
    assert error == "polygon_news_unavailable"
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured


def test_crash_monitor_never_puts_polygon_key_in_url_or_logs(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(api, "POLYGON_KEY", "SUPERSECRET")
    monkeypatch.setattr(api, "CRASH_MONITOR_CACHE", str(tmp_path / "crash.json"))
    seen = []

    def _raise(url, *args, **kwargs):
        seen.append((url, kwargs.get("params") or {}))
        raise RuntimeError(f"GET failed {url}?apiKey=SUPERSECRET&token=ALSOSECRET")

    monkeypatch.setattr(api, "rate_limited_get", _raise)
    api._crash_monitor_wrapper()

    assert seen
    assert all("SUPERSECRET" not in url for url, _ in seen)
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured


def test_orb_reference_failure_returns_stable_reason(monkeypatch, capsys):
    api._ORB_REFERENCE_CACHE.clear()
    monkeypatch.setattr(api, "POLYGON_KEY", "configured")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )

    allowed, reason = api._is_orb_common_stock_candidate("SAFE")

    assert allowed is False
    assert reason == "reference_unavailable"
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured


def test_scanner_health_never_returns_raw_last_error(monkeypatch, tmp_path):
    cache = tmp_path / "scanner.json"
    cache.write_text("{}", encoding="utf-8")
    monkeypatch.setitem(api.SCAN_CACHE_MAP, "secret_scan", str(cache))

    status = api._scan_cache_health(
        "secret_scan", {"last_error": _CANARY, "interval_min": 5}
    )

    assert status["scan_error"] == "scan_failed"
    assert "SUPERSECRET" not in str(status)
    assert "ALSOSECRET" not in str(status)


def test_calendar_fatal_error_is_stable_and_log_redacted(monkeypatch, capsys):
    monkeypatch.setattr(
        api,
        "_build_exchange_calendar_status",
        lambda: (_ for _ in ()).throw(RuntimeError(_CANARY)),
    )

    payload = api.get_economic_calendar()

    assert payload["status"] == "error"
    assert payload["message"] == "economic_calendar_unavailable"
    assert "SUPERSECRET" not in str(payload)
    captured = capsys.readouterr().out
    assert "SUPERSECRET" not in captured
    assert "ALSOSECRET" not in captured
    assert "<redacted>" in captured
