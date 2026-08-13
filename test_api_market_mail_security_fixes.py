"""Focused regressions for market-evidence, delivery and provider boundaries."""

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
import requests

import api
from modules import auth
from modules import new_listing_scanner as nls
from modules import smart_money_radar as smr


NOW = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc).timestamp()


def _stock_row(scan_ts=NOW - 600):
    return {
        "ticker": "AAA",
        "Ticker": "AAA",
        "Strategy": "Momentum Breakout Long",
        "Signal_Direction": "LONG",
        "grade": "A",
        "score": 91,
        "RVOL": 2.5,
        "price": 100.0,
        "Preis": 100.0,
        "Prev_Close": 98.0,
        "trade_setup": {
            "direction": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "tp1": 110.0,
            "tp2": 120.0,
        },
        "scan_price_observed_at": datetime.fromtimestamp(
            scan_ts, timezone.utc
        ).isoformat(),
        "scan_price_source": "polygon_snapshot",
    }


def _path(scan_ts, quote_ts, bars=None):
    bars = bars or [
        {"timestamp": scan_ts, "high": 104.0, "low": 96.0},
        {"timestamp": quote_ts - 50, "high": 104.0, "low": 96.0},
    ]
    return {
        "ok": True,
        "bars": bars,
        "first_timestamp": bars[0]["timestamp"],
        "last_timestamp": bars[-1]["timestamp"],
        "source": "polygon_1m_aggs",
    }


def test_trade_reminder_unknown_smtp_is_terminal_and_never_auto_retried(monkeypatch):
    reminder = {
        "id": "rem-unknown-1",
        "owner_email": "owner@example.invalid",
        "ticker": "ETH",
        "asset_type": "crypto",
        "channel": "email_browser",
        "row": {"entry": 100, "stop": 95, "tp1": 110, "tp2": 120},
    }
    calls = []
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(
        api,
        "_load_users",
        lambda: {
            "users": {
                "owner@example.invalid": {
                    "alert_email": "owner@example.invalid",
                    "email_alerts_enabled": True,
                }
            }
        },
    )

    def _unknown_delivery(*args, **kwargs):
        calls.append(kwargs)
        api._set_last_delivery_outcome("unknown")
        return False

    monkeypatch.setattr(api, "_send_email_alert", _unknown_delivery)

    assert api._deliver_trade_reminder_email(reminder, {}, now=1_000.0) is False
    assert reminder["email_delivery_status"] == "uncertain_manual_reconciliation"
    assert reminder["email_delivery_reason"] == "smtp_data_outcome_unknown"
    assert reminder["email_delivery_manual_reconciliation_required"] is True
    assert "next_email_attempt_at" not in reminder
    assert calls[0]["delivery_dedupe_keys"] == ["personal-reminder:rem-unknown-1"]

    assert api._deliver_trade_reminder_email(reminder, {}, now=9_000.0) is False
    assert len(calls) == 1
    assert reminder["email_attempts"] == 1


def test_stock_market_path_rejects_an_internal_missing_minute(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104, "l": 96},
                    {"t": int((NOW - 60) * 1000), "h": 104, "l": 96},
                ]
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA", NOW - 180, now_ts=NOW
    )

    assert result == {"ok": False, "reason": "final_market_path_internal_gap"}


def test_stock_quote_is_reaged_after_slow_path_io(monkeypatch):
    quote_ts = NOW - 89
    monotonic = iter([100.0, 110.0])
    monkeypatch.setattr(api.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote_ts,
            "receipt_ts": NOW,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: _path(NOW - 600, quote_ts),
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _stock_row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_quote_stale_after_path"}


def test_stock_receipt_session_is_checked_after_path_io(monkeypatch):
    receipt_ts = datetime(2026, 8, 13, 19, 59, 55, tzinfo=timezone.utc).timestamp()
    quote_ts = receipt_ts - 5
    scan_ts = quote_ts - 600
    monotonic = iter([200.0, 210.0])
    monkeypatch.setattr(api.time, "monotonic", lambda: next(monotonic))
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote_ts,
            "receipt_ts": receipt_ts,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: _path(scan_ts, quote_ts),
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _stock_row(scan_ts), now_ts=receipt_ts, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_receipt_session_mismatch"}


def _patch_penny_candidate(monkeypatch, *, trigger_ts, path_bars):
    monkeypatch.setattr(
        api,
        "_penny_fetch_live_spread",
        lambda *_args, **_kwargs: {
            "bid": 99.8,
            "ask": 100.2,
            "spread_bps": 40.0,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "quote_age_seconds": 10.0,
        },
    )
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **k: [])
    monkeypatch.setattr(
        api,
        "analyze_penny_intraday",
        lambda *a, **k: {
            "trigger_confirmed": True,
            "fresh": True,
            "age_seconds": NOW - (trigger_ts + 300),
            "trigger_timestamp": trigger_ts,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *a, **k: _path(trigger_ts + 300, NOW - 10, path_bars),
    )
    monkeypatch.setattr(api.time, "monotonic", lambda: 100.0)


def _penny_row():
    return {
        "ticker": "PENNY",
        "trade_setup": {
            "entry": 100.0,
            "stop_loss": 95.0,
            "tp1": 110.0,
            "tp2": 120.0,
        },
    }


def test_penny_revalidation_blocks_stop_touch_since_closed_trigger(monkeypatch):
    source_ts = NOW - 60
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[{"timestamp": source_ts, "high": 104.0, "low": 94.0}],
    )

    candidate, reason = api._penny_revalidate_buy_candidate(_penny_row(), now_ts=NOW)

    assert candidate is None
    assert reason == "final_stop_touched_since_trigger"


def test_penny_revalidation_rejects_internal_causal_path_gap(monkeypatch):
    source_ts = NOW - 180
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[
            {"timestamp": source_ts, "high": 104.0, "low": 96.0},
            {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
        ],
    )

    candidate, reason = api._penny_revalidate_buy_candidate(_penny_row(), now_ts=NOW)

    assert candidate is None
    assert reason == "final_market_path_internal_gap"


@pytest.mark.parametrize("venue", [None, "kraken", "UNKNOWN"])
def test_unknown_crypto_venue_never_falls_back_to_cryptocom(monkeypatch, venue):
    def _unexpected(*_args, **_kwargs):
        pytest.fail("unknown venue reached Crypto.com adapter")

    monkeypatch.setattr(nls, "fetch_cryptocom_ticker", _unexpected)
    monkeypatch.setattr(nls, "fetch_cryptocom_candles", _unexpected)
    monkeypatch.setattr(nls, "fetch_cryptocom_orderbook", _unexpected)

    assert nls.fetch_ticker_for("BTC_USDT", venue) is None
    assert nls.fetch_candles_for("BTC_USDT", venue) == []
    assert nls.fetch_orderbook_for("BTC_USDT", venue) is None
    with pytest.raises(ValueError, match="unsupported_market_venue"):
        nls._monitor_key("BTC_USDT", venue)


def test_unknown_crypto_venue_is_not_added_to_monitoring(monkeypatch):
    writes = []
    monkeypatch.setattr(nls, "load_monitoring_list", lambda: {})
    monkeypatch.setattr(nls, "save_monitoring_list", lambda value: writes.append(value))

    assert nls.add_to_monitoring("BTC_USDT", "kraken") == {}
    assert writes == []


def test_stock_waves_http_error_is_stable_and_key_is_redacted(
    monkeypatch, tmp_path, caplog
):
    canary = "RADAR_SECRET_CANARY"
    error = requests.HTTPError(
        f"403 Client Error for url: https://provider.invalid/data?apiKey={canary}"
    )
    monkeypatch.setattr(smr, "_polygon_market_snapshot", lambda _key: (_ for _ in ()).throw(error))
    caplog.set_level(logging.WARNING, logger="bg_service")

    result = smr.fetch_stock_waves("real-key", history_path=str(tmp_path / "v.json"))
    log_text = caplog.text

    assert result == {
        "status": "error",
        "error": "stock_waves_unavailable",
        "waves": [],
    }
    assert canary not in json.dumps(result)
    assert canary not in log_text
    assert "[REDACTED]" in log_text


def test_whale_error_is_stable_and_key_is_redacted(monkeypatch, caplog):
    canary = "WHALE_SECRET_CANARY"
    error = requests.HTTPError(
        f"401 Client Error for url: https://provider.invalid/tx?api_key={canary}"
    )
    monkeypatch.setattr(smr, "_http_get_json", lambda *a, **k: (_ for _ in ()).throw(error))
    caplog.set_level(logging.WARNING, logger="bg_service")

    result = smr.fetch_whale_alerts("real-key")

    assert result["error"] == "whale_alerts_unavailable"
    assert canary not in json.dumps(result)
    assert canary not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_legacy_radar_cache_cannot_republish_provider_exception(tmp_path):
    canary = "CACHED_RADAR_SECRET_CANARY"
    cache_path = tmp_path / "radar.json"
    cache_path.write_text(
        json.dumps(
            {
                "_cached_at": api.time.time(),
                "cache": "new",
                "sections": {
                    "stock_waves": {
                        "status": "error",
                        "error": f"403 https://provider.invalid/?apiKey={canary}",
                        "partial_errors": [f"prepared url apiKey={canary}"],
                        "waves": [],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = smr.build_radar(cache_path=str(cache_path), ttl_sec=3600)

    serialized = json.dumps(result)
    assert canary not in serialized
    assert result["sections"]["stock_waves"]["error"] == "stock_waves_unavailable"
    assert result["sections"]["stock_waves"]["partial_errors"] == [
        "provider_failure_1"
    ]


def test_radar_api_never_returns_or_prints_provider_url_secret(monkeypatch, capsys):
    canary = "API_RADAR_SECRET_CANARY"

    def _raise(**_kwargs):
        raise requests.HTTPError(
            f"403 for https://provider.invalid/data?apiKey={canary}"
        )

    monkeypatch.setattr(api, "_smart_money_build_radar", _raise)
    response = api.get_smart_money_radar(refresh=1)
    output = capsys.readouterr().out
    body = json.loads(response.body)

    assert response.status_code == 503
    assert body["error"] == "smart_money_radar_unavailable"
    assert canary not in json.dumps(body)
    assert canary not in output
    assert "redacted" in output.lower()


def test_radar_api_scrubs_raw_legacy_builder_payload(monkeypatch):
    canary = "API_LEGACY_CACHE_CANARY"
    monkeypatch.setattr(
        api,
        "_smart_money_build_radar",
        lambda **_kwargs: {
            "cache": "fresh",
            "sections": {
                "stock_waves": {
                    "status": "error",
                    "error": f"prepared url ?apiKey={canary}",
                    "waves": [],
                }
            },
        },
    )

    response = api.get_smart_money_radar()
    body = json.loads(response.body)

    assert response.status_code == 200
    assert canary not in json.dumps(body)
    assert body["sections"]["stock_waves"]["error"] == "stock_waves_unavailable"


def test_auth_checkout_log_uses_irreversible_user_key(monkeypatch, capsys):
    email = "AUTH-CANARY-PERSON@example.invalid"
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_SECRET_KEY", "configured")
    monkeypatch.setattr(
        auth,
        "get_checkout_eligibility",
        lambda *_args: {"allowed": False, "code": "active_subscription"},
    )

    assert auth.create_checkout_session(email, "pro", "ok", "cancel") is None
    output = capsys.readouterr().out

    assert email.lower() not in output.lower()
    assert auth._auth_user_log_key(email) in output


@pytest.mark.parametrize(
    ("event_type", "event_data"),
    [
        (
            "checkout.session.completed",
            {
                "metadata": {"email": "AUTH-WEBHOOK-CANARY@example.invalid", "plan": "pro"},
                "payment_status": "paid",
                "subscription": "sub_1",
                "customer": "cus_1",
            },
        ),
        (
            "customer.subscription.updated",
            {
                "id": "sub_1",
                "customer": "cus_1",
                "status": "active",
                "items": {"data": [{"price": {"id": "price_pro"}}]},
            },
        ),
        (
            "customer.subscription.updated",
            {"id": "sub_1", "customer": "cus_1", "status": "canceled"},
        ),
        (
            "customer.subscription.deleted",
            {"id": "sub_1", "customer": "cus_1"},
        ),
    ],
)
def test_auth_stripe_lifecycle_logs_never_contain_email(
    monkeypatch, capsys, event_type, event_data
):
    email = "AUTH-WEBHOOK-CANARY@example.invalid"
    user = {
        "id": "user-1",
        "plan": "pro",
        "stripe_customer_id": "cus_1",
        "stripe_subscription_id": "sub_1",
    }
    event = {
        "id": f"evt-{event_type}-{event_data.get('status', 'once')}",
        "type": event_type,
        "created": int(NOW),
        "data": {"object": event_data},
    }
    monkeypatch.setattr(auth, "HAS_STRIPE", True)
    monkeypatch.setattr(auth, "STRIPE_WEBHOOK_SECRET", "configured")
    monkeypatch.setattr(
        auth.stripe.Webhook,
        "construct_event",
        staticmethod(lambda *_args: event),
    )
    monkeypatch.setattr(auth, "_load_processed_webhook_events", lambda: [])
    monkeypatch.setattr(auth, "_remember_webhook_event", lambda _event_id: None)
    monkeypatch.setattr(auth, "_load_users", lambda: {"users": {email.lower(): user}})
    monkeypatch.setattr(
        auth,
        "STRIPE_PRICE_IDS",
        {"trial": "price_trial", "pro_monthly": "price_pro"},
    )

    def _update(email_key, mutator):
        assert email_key == email.lower()
        mutator(user)
        return user

    monkeypatch.setattr(auth, "_update_user_atomic", _update)

    result = auth.handle_stripe_webhook(b"payload", "signature")
    output = capsys.readouterr().out

    assert result["success"] is True
    assert email.lower() not in output.lower()
    assert auth._auth_user_log_key(email) in output


def test_smart_money_page_uses_external_csp_compatible_script():
    page_response = asyncio.run(api.serve_smart_money_page())
    script_response = asyncio.run(api.serve_smart_money_script())
    page = page_response.body.decode("utf-8")
    script = script_response.body.decode("utf-8")
    api_source = Path(api.__file__).read_text(encoding="utf-8")

    assert '<script src="/smart_money.js" defer></script>' in page
    assert re.search(r"<script(?![^>]*\bsrc=)[^>]*>", page, re.IGNORECASE) is None
    assert "escapeHtml" in script
    assert "application/javascript" in script_response.media_type
    assert "script-src 'self'" in api_source
    assert "script-src 'self' 'unsafe-inline'" not in api_source
