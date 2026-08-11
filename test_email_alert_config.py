import json
import time

import api


def test_email_config_can_be_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("GMAIL_USER", "sender@example.com")
    monkeypatch.setenv("GMAIL_APP_PASSWORD", "app-password")
    monkeypatch.setenv("ALERT_EMAIL", "one@example.com, two@example.com")

    secrets = api._load_secrets()

    assert secrets["GMAIL_USER"] == "sender@example.com"
    assert secrets["GMAIL_APP_PASSWORD"] == "app-password"
    assert secrets["ALERT_EMAIL"] == "one@example.com, two@example.com"


def test_email_status_never_exposes_credentials(monkeypatch):
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "_SECRETS", {
        "GMAIL_USER": "sender@example.com",
        "GMAIL_APP_PASSWORD": "secret-app-password",
        "ALERT_EMAIL": "one@example.com,two@example.com",
    })

    status = api._email_alert_status()

    assert status["configured"] is True
    assert status["recipient_count"] == 2
    assert "secret-app-password" not in str(status)
    assert "sender@example.com" not in str(status)


def test_alert_email_uses_alpha_station_brand_shell():
    branded = api._brand_email_html(
        "Crypto Early Mover LONG",
        "<html><body><h2>TradingBot Alert</h2><p>Setup ready</p></body></html>",
    )

    assert "Alpha Station" in branded
    assert "Trading Intelligence" in branded
    assert "Crypto Early Mover LONG" in branded
    assert "Setup ready" in branded
    assert "TradingBot Alert" not in branded
    assert "Keine Anlageberatung" in branded


def test_pump_dump_scheduler_uses_intraday_execution_cadence():
    assert api._scan_status["new_listing"]["interval_min"] <= 15


def test_common_stock_guard_uses_stale_cache_when_reference_unavailable(tmp_path, monkeypatch):
    cache_file = tmp_path / "common_stock.json"
    cache_file.write_text(json.dumps({
        "cached_at": time.time() - 3 * 86400,
        "tickers": ["AAPL", "AQST"],
    }))

    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache_file))
    monkeypatch.setattr(api, "POLYGON_KEY", "")
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {"loaded_at": 0, "tickers": None, "source": "not_loaded"})

    tickers, source = api._load_common_stock_universe(max_age_seconds=1)

    assert tickers == {"AAPL", "AQST"}
    assert source == "stale_file_cache"


def test_common_stock_guard_redacts_polygon_key_from_fetch_errors(tmp_path, monkeypatch, capsys):
    polygon_key = "polygon-secret-for-redaction-test"
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(api, "POLYGON_KEY", polygon_key)
    monkeypatch.setattr(
        api,
        "_COMMON_STOCK_UNIVERSE_MEM",
        {"loaded_at": 0, "tickers": None, "source": "not_loaded"},
    )

    def fail_request(url, params=None, timeout=None):
        raise RuntimeError(
            "request failed: "
            f"https://api.polygon.io/v3/reference/tickers?apiKey={polygon_key}&market=stocks"
        )

    monkeypatch.setattr(api, "rate_limited_get", fail_request)

    tickers, source = api._load_common_stock_universe(max_age_seconds=1)
    output = capsys.readouterr().out

    assert tickers is None
    assert source == "unavailable"
    assert polygon_key not in output
    assert "apiKey=<redacted>" in output


def test_email_status_reports_common_stock_guard_without_keys(tmp_path, monkeypatch):
    cache_file = tmp_path / "common_stock.json"
    cache_file.write_text(json.dumps({
        "cached_at": time.time(),
        "tickers": ["AAPL"],
    }))

    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache_file))
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", {"loaded_at": 0, "tickers": None, "source": "not_loaded"})
    monkeypatch.setattr(api, "_require_admin", lambda authorization: ({}, "admin@example.com"))

    status = api.get_email_alert_status("Bearer admin-token")

    assert status["common_stock_guard"]["available"] is True
    assert status["common_stock_guard"]["ticker_count"] == 1
    assert "unit-key" not in str(status)
