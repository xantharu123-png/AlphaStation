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


def test_stock_market_path_preserves_provider_empty_minute_without_synthetic_bar(monkeypatch):
    class AggregateResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104, "l": 96},
                    {"t": int((NOW - 60) * 1000), "h": 104, "l": 96},
                ]
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    calls = []

    def _get(url, *args, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        if "/v3/trades/" in url:
            class EmptyTradeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"status": "OK", "results": []}

            return EmptyTradeResponse()
        return AggregateResponse()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA", NOW - 180, now_ts=NOW
    )

    assert result["ok"] is True
    assert [bar["timestamp"] for bar in result["bars"]] == [NOW - 180, NOW - 60]
    assert result["provider_empty_aggregate_interval_count"] == 1
    assert result["provider_empty_aggregate_intervals"] == [
        {
            "start_timestamp": NOW - 120,
            "end_timestamp": NOW - 60,
            "proof": "bounded_trade_replay_no_trade",
        }
    ]
    # No flat/interpolated candle is invented for the empty minute.
    assert len(result["bars"]) == 2
    assert len(calls) == 2
    assert "/v2/aggs/" in calls[0][0]
    assert "/v3/trades/" in calls[1][0]


def test_stock_market_path_accepts_fully_elapsed_leading_provider_empty_minutes(
    monkeypatch,
):
    # 10:00:30 observation, then no qualifying trade until the genuine 10:02 bar.
    observation = NOW - 210
    first_real_bar = NOW - 120

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {
                        "t": int(first_real_bar * 1000),
                        "h": 104.0,
                        "l": 96.0,
                    }
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    def _get(url, *args, **kwargs):
        if "/v3/trades/" in url:
            class EmptyTradeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"status": "OK", "results": []}

            return EmptyTradeResponse()
        return Response()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        observation,
        now_ts=NOW - 60,
    )

    assert result["ok"] is True
    assert result["bars"] == [
        {"timestamp": first_real_bar, "high": 104.0, "low": 96.0}
    ]
    assert result["provider_empty_aggregate_intervals"][0] == {
        "start_timestamp": observation,
        "end_timestamp": first_real_bar,
        "proof": "bounded_trade_replay_no_trade",
    }
    assert len(result["bars"]) == 1


@pytest.mark.parametrize(
    ("scan_ts", "quote_ts", "aggregate_bars", "trade_ts", "trade_price", "reason"),
    [
        (
            NOW - 180,
            NOW - 10,
            [
                {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 96.0},
            ],
            NOW - 61,
            94.0,
            "final_stop_touched_since_scan",
        ),
        (
            NOW - 210,
            NOW - 60,
            [
                {"t": int((NOW - 120) * 1000), "h": 104.0, "l": 96.0},
            ],
            NOW - 180,
            111.0,
            "final_tp1_touched_since_scan",
        ),
    ],
)
def test_stock_revalidation_reconstructs_leading_or_internal_gap_touch_from_raw(
    monkeypatch,
    scan_ts,
    quote_ts,
    aggregate_bars,
    trade_ts,
    trade_price,
    reason,
):
    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    calls = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if "/v3/trades/" in url:
            return Response({
                "status": "OK",
                "results": [{
                    "sip_timestamp": int(trade_ts * 1_000_000_000),
                    "price": trade_price,
                }],
            })
        return Response({"status": "OK", "results": aggregate_bars})

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", _get)
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote_ts,
            "receipt_ts": NOW,
            "last_trade_ts": trade_ts,
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _stock_row(scan_ts), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": reason}
    assert len([url for url in calls if "/v3/trades/" in url]) == 1


def test_stock_market_path_replays_positive_half_second_trailing_gap(monkeypatch):
    observed_ts = NOW - 60
    quote_ts = NOW + 0.5
    last_trade_ts = NOW + 0.25

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    calls = []

    def _get(url, *args, **kwargs):
        calls.append(url)
        if "/v3/trades/" in url:
            return Response({
                "status": "OK",
                "results": [{
                    "sip_timestamp": int(last_trade_ts * 1_000_000_000),
                    "price": 94.0,
                }],
            })
        return Response({
            "status": "OK",
            "results": [
                {"t": int(observed_ts * 1000), "h": 104.0, "l": 96.0},
                {"t": int(NOW * 1000), "h": 104.0, "l": 99.0},
            ],
        })

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        observed_ts,
        now_ts=quote_ts,
        last_trade_ts=last_trade_ts,
    )

    assert result["ok"] is True, result
    assert result["bars"][-1]["low"] == pytest.approx(94.0)
    assert result["bars"][-1]["evidence_source"] == "polygon_bounded_trades"
    assert len([url for url in calls if "/v3/trades/" in url]) == 1


@pytest.mark.parametrize(
    ("payload_updates", "expected_reason"),
    [
        ({"status": "DELAYED"}, "final_market_path_not_realtime"),
        ({"status": None}, "final_market_path_not_realtime"),
        ({"next_url": "https://api.polygon.io/cursor"}, "final_market_path_truncated"),
        ({"results": {}}, "final_market_path_payload_invalid"),
        ({"results": ""}, "final_market_path_payload_invalid"),
        ({"results": False}, "final_market_path_payload_invalid"),
        ({"results": 0}, "final_market_path_payload_invalid"),
    ],
)
def test_stock_market_path_rejects_noncurrent_or_paginated_aggregate_evidence(
    monkeypatch, payload_updates, expected_reason
):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            payload = {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104, "l": 96},
                    {"t": int((NOW - 60) * 1000), "h": 104, "l": 96},
                ],
            }
            payload.update(payload_updates)
            return payload

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA", NOW - 180, now_ts=NOW
    )

    assert result == {"ok": False, "reason": expected_reason}


def test_stock_market_path_current_minute_bar_closes_touch_rebound_gap_with_one_call(
    monkeypatch,
):
    watermark = NOW - 10
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    # The current minute crossed TP1 and rebounded before the
                    # executable quote. These are provider-emitted prices.
                    {"t": int((NOW - 60) * 1000), "h": 111.0, "l": 99.0},
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

    def _get(url, *args, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        if "/v3/trades/" in url:
            class TradeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {
                        "status": "OK",
                        "results": [{
                            "sip_timestamp": int((NOW - 61) * 1_000_000_000),
                            "price": 100.0,
                        }],
                    }

            return TradeResponse()
        return Response()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=NOW - 61,
    )

    assert result["ok"] is True
    assert result["current_minute_aggregate_used"] is True
    assert result["bars"][-1] == {
        "timestamp": NOW - 60,
        "high": 111.0,
        "low": 99.0,
    }
    assert len(calls) == 2
    assert "/v2/aggs/" in calls[0][0]
    assert len([url for url, _params in calls if "/v3/trades/" in url]) == 1


def test_stock_market_path_replaces_lagging_current_bar_with_complete_raw_trades(
    monkeypatch,
):
    watermark = NOW - 10
    snapshot_last_trade = watermark - 1
    calls = []

    class AggregateResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    # This aggregate is harmless but lags the snapshot trade.
                    {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 99.0},
                ],
            }

    class TradeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {
                        "sip_timestamp": int((watermark - 20) * 1_000_000_000),
                        "price": 94.0,
                    },
                    {
                        "sip_timestamp": int(snapshot_last_trade * 1_000_000_000),
                        "price": 100.0,
                    },
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

    def _get(url, *args, **kwargs):
        calls.append((url, kwargs.get("params") or {}))
        return TradeResponse() if "/v3/trades/" in url else AggregateResponse()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=snapshot_last_trade,
    )

    assert result["ok"] is True
    assert result["bars"][-1]["timestamp"] == NOW - 60
    assert result["bars"][-1]["high"] == pytest.approx(100.0)
    assert result["bars"][-1]["low"] == pytest.approx(94.0)
    assert result["bars"][-1]["evidence_source"] == "polygon_bounded_trades"
    assert result["trailing_raw_trade_evidence_used"] is True
    assert result["trailing_raw_trade_count"] == 2
    assert result["current_minute_aggregate_used"] is False
    assert len(calls) == 2
    raw_calls = [(url, params) for url, params in calls if "/v3/trades/" in url]
    assert len(raw_calls) == 1
    raw_url, raw_params = raw_calls[0]
    assert "apiKey" not in raw_url
    assert raw_params["timestamp.gte"] == int((NOW - 120) * 1_000_000_000)
    assert raw_params["timestamp.lte"] == int(watermark * 1_000_000_000)
    assert raw_params["limit"] == 50_000


def test_stock_market_path_blocks_when_raw_trade_feed_does_not_reach_snapshot(
    monkeypatch,
):
    watermark = NOW - 10
    snapshot_last_trade = watermark - 1
    calls = []

    class AggregateResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 99.0},
                ],
            }

    class LaggingTradeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {
                        "sip_timestamp": int((watermark - 5) * 1_000_000_000),
                        "price": 100.0,
                    }
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

    def _get(url, *args, **kwargs):
        calls.append(url)
        return (
            LaggingTradeResponse()
            if "/v3/trades/" in url
            else AggregateResponse()
        )

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=snapshot_last_trade,
    )

    assert result == {
        "ok": False,
        "reason": "final_market_path_trailing_trade_watermark_not_reached",
    }
    assert len([url for url in calls if "/v3/trades/" in url]) == 1


@pytest.mark.parametrize(
    ("raw_updates", "expected_reason"),
    [
        (
            {"status": None},
            "final_market_path_trailing_trades_not_realtime",
        ),
        (
            {"next_url": "https://api.polygon.io/v3/trades/AAA?cursor=secret"},
            "final_market_path_trailing_trades_truncated",
        ),
        (
            {"results": {}},
            "final_market_path_trailing_trades_payload_invalid",
        ),
        (
            {"results": ""},
            "final_market_path_trailing_trades_payload_invalid",
        ),
        (
            {"results": False},
            "final_market_path_trailing_trades_payload_invalid",
        ),
        (
            {"results": 0},
            "final_market_path_trailing_trades_payload_invalid",
        ),
    ],
)
def test_stock_market_path_rejects_incomplete_raw_trade_evidence(
    monkeypatch, raw_updates, expected_reason
):
    watermark = NOW - 10

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    aggregate_payload = {
        "status": "OK",
        "results": [
            {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
            {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 99.0},
        ],
    }
    trade_payload = {
        "status": "OK",
        "results": [
            {
                "sip_timestamp": int((watermark - 1) * 1_000_000_000),
                "price": 100.0,
            }
        ],
    }
    trade_payload.update(raw_updates)
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda url, *args, **kwargs: Response(
            trade_payload if "/v3/trades/" in url else aggregate_payload
        ),
    )

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=watermark - 1,
    )

    assert result == {"ok": False, "reason": expected_reason}


def test_stock_market_path_fails_closed_without_raw_trade_entitlement(monkeypatch):
    watermark = NOW - 10

    class AggregateResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 99.0},
                ],
            }

    class ForbiddenTradeResponse:
        status_code = 403

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda url, *args, **kwargs: (
            ForbiddenTradeResponse()
            if "/v3/trades/" in url
            else AggregateResponse()
        ),
    )

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=watermark - 1,
    )

    assert result == {
        "ok": False,
        "reason": "final_market_path_trailing_trades_http_403",
    }


def test_stock_market_path_accepts_empty_trailing_gap_only_with_snapshot_watermark(
    monkeypatch,
):
    watermark = NOW - 10
    calls = []

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    {"t": int((NOW - 120) * 1000), "h": 104.0, "l": 96.0},
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

    def _get(url, *args, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        # The latest snapshot trade predates the missing trailing minute.
        last_trade_ts=NOW - 61,
    )

    assert result["ok"] is True
    assert result["trailing_no_trade_evidence"] == {
        "start_timestamp": NOW - 60,
        "end_timestamp": watermark,
        "proof": "snapshot_last_trade_before_trailing_gap",
        "last_trade_timestamp": NOW - 61,
    }
    assert result["current_minute_aggregate_used"] is False
    assert len(calls) == 1


def test_stock_market_path_rejects_unverified_trade_inside_missing_trailing_gap(
    monkeypatch,
):
    watermark = NOW - 10

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 180) * 1000), "h": 104.0, "l": 96.0},
                    {"t": int((NOW - 120) * 1000), "h": 104.0, "l": 96.0},
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

    def _get(url, *args, **kwargs):
        if "/v3/trades/" in url:
            class EmptyTradeResponse:
                status_code = 200

                @staticmethod
                def json():
                    return {"status": "OK", "results": []}

            return EmptyTradeResponse()
        return Response()

    monkeypatch.setattr(api, "rate_limited_get", _get)

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        NOW - 180,
        now_ts=watermark,
        last_trade_ts=NOW - 30,
    )

    assert result == {
        "ok": False,
        "reason": "final_market_path_trailing_trade_watermark_not_reached",
    }


def test_stock_market_path_accepts_all_empty_only_when_last_trade_predates_observation(
    monkeypatch,
):
    observation = NOW - 180

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "OK"}

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        observation,
        now_ts=NOW - 10,
        last_trade_ts=observation - 1,
    )

    assert result["ok"] is True
    assert result["bars"] == []
    assert result["trailing_no_trade_evidence"]["proof"] == (
        "snapshot_last_trade_before_observation"
    )


@pytest.mark.parametrize(
    ("last_trade_ts", "expected_reason"),
    [
        (None, "final_market_path_trailing_gap_unverified"),
        (NOW - 120, "final_market_path_trailing_trade_watermark_not_reached"),
    ],
)
def test_stock_market_path_rejects_all_empty_without_prior_trade_watermark(
    monkeypatch, last_trade_ts, expected_reason
):
    observation = NOW - 180

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "OK", "results": []}

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA",
        observation,
        now_ts=NOW - 10,
        last_trade_ts=last_trade_ts,
    )

    assert result == {
        "ok": False,
        "reason": expected_reason,
    }


def test_stock_market_path_rejects_result_limit_even_without_cursor(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"status": "OK", "results": [{}] * 5000}

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA", NOW - 180, now_ts=NOW
    )

    assert result == {
        "ok": False,
        "reason": "final_market_path_result_limit_reached",
    }


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
            "last_trade_ts": quote_ts - 1,
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
            "last_trade_ts": quote_ts - 1,
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
            "last_trade_ts": NOW - 61,
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


def test_penny_live_spread_reuses_atomic_snapshot_trade_watermark(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 11,
        },
    )

    result = api._penny_fetch_live_spread("PENNY")

    assert result is not None
    assert result["bid"] == pytest.approx(99.8)
    assert result["ask"] == pytest.approx(100.2)
    assert result["last_trade_ts"] == NOW - 11


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


def test_penny_revalidation_passes_atomic_snapshot_trade_watermark_to_path(
    monkeypatch,
):
    source_ts = NOW - 60
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[{"timestamp": source_ts, "high": 104.0, "low": 96.0}],
    )
    captured = []

    def _path(*args, **kwargs):
        captured.append(kwargs)
        return {"ok": False, "reason": "unit_path_stop"}

    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert candidate is None
    assert reason == "unit_path_stop"
    assert captured == [{"now_ts": NOW - 10, "last_trade_ts": NOW - 61}]


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


def test_penny_revalidation_accepts_provider_empty_aggregate_gap_coverage(monkeypatch):
    source_ts = NOW - 180
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[
            {"timestamp": source_ts, "high": 104.0, "low": 96.0},
            {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
        ],
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": source_ts, "high": 104.0, "low": 96.0},
                {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
            ],
            "source": "polygon_1m_aggs_with_provider_empty_intervals",
            "first_timestamp": source_ts,
            "last_timestamp": NOW - 60,
            "coverage_verified": True,
            "coverage_start_timestamp": source_ts,
            "coverage_end_timestamp": NOW - 10,
            "provider_empty_aggregate_interval_count": 1,
            "provider_empty_aggregate_intervals": [
                {
                    "start_timestamp": source_ts + 60,
                    "end_timestamp": NOW - 60,
                    "proof": "massive_no_qualifying_trade_aggregate",
                }
            ],
        },
    )
    monkeypatch.setattr(
        api,
        "estimate_penny_execution_costs",
        lambda **kwargs: {
            "max_order_notional": 1_000.0,
            "slippage_bps": 10.0,
            "execution_cost_bps": 60.0,
        },
    )

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert reason == "ok"
    assert candidate is not None
    assert candidate["final_market_path_bars"] == 2
    assert candidate["final_market_path_provider_empty_intervals"] == 1
    assert candidate["final_market_path_source"].endswith(
        "provider_empty_intervals"
    )


def _patch_penny_execution_costs(monkeypatch):
    monkeypatch.setattr(
        api,
        "estimate_penny_execution_costs",
        lambda **kwargs: {
            "max_order_notional": 1_000.0,
            "slippage_bps": 10.0,
            "execution_cost_bps": 40.0,
        },
    )


def test_penny_revalidation_prices_entry_from_q2_not_q1(monkeypatch):
    source_ts = NOW - 60
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[{"timestamp": source_ts, "high": 104.0, "low": 96.0}],
    )
    _patch_penny_execution_costs(monkeypatch)
    quotes = iter([
        {
            "bid": 99.8,
            "ask": 100.0,
            "spread_bps": 20.0,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 61,
        },
        {
            "bid": 100.8,
            "ask": 101.0,
            "spread_bps": 20.0,
            "observed_ts": NOW - 5,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 61,
        },
    ])
    monkeypatch.setattr(
        api, "_penny_fetch_live_spread", lambda *_a, **_k: next(quotes)
    )

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert reason == "ok"
    assert candidate is not None
    assert candidate["price"] == pytest.approx(101.0)
    assert candidate["trade_setup"]["entry"] == pytest.approx(101.0)
    assert candidate["final_market_path_round_count"] == 1


def _patch_penny_advancing_handshake(monkeypatch, *, final_watermark="bounded"):
    source_ts = NOW - 180
    trigger_ts = source_ts - 300
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=trigger_ts,
        path_bars=[{"timestamp": source_ts, "high": 104.0, "low": 96.0}],
    )
    _patch_penny_execution_costs(monkeypatch)
    q1_ts = NOW - 20
    q2_ts = NOW - 10
    q3_ts = NOW - 5
    q2_last_trade = NOW - 11
    final_last_trade = (
        None
        if final_watermark == "missing"
        else NOW - 6
        if final_watermark == "unbounded"
        else q2_last_trade
    )
    quotes = iter([
        {
            "bid": 99.8,
            "ask": 100.0,
            "spread_bps": 20.0,
            "observed_ts": q1_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q1_ts - 1,
        },
        {
            "bid": 100.3,
            "ask": 100.5,
            "spread_bps": 20.0,
            "observed_ts": q2_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q2_last_trade,
        },
        {
            "bid": 100.8,
            "ask": 101.0,
            "spread_bps": 20.0,
            "observed_ts": q3_ts,
            "receipt_ts": NOW,
            "last_trade_ts": final_last_trade,
        },
    ])
    monkeypatch.setattr(
        api, "_penny_fetch_live_spread", lambda *_a, **_k: next(quotes)
    )
    path_calls = []

    def _market_path(_ticker, observed_ts, **kwargs):
        path_calls.append((observed_ts, kwargs["now_ts"], kwargs["last_trade_ts"]))
        return {
            "ok": True,
            "bars": [
                {"timestamp": observed_ts, "high": 104.0, "low": 96.0}
            ],
            "source": "unit_path",
            "first_timestamp": observed_ts,
            "last_timestamp": observed_ts,
            "coverage_verified": True,
            "coverage_start_timestamp": observed_ts,
            "coverage_end_timestamp": kwargs["now_ts"],
        }

    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _market_path)
    return path_calls


def test_penny_revalidation_incrementally_extends_path_and_uses_qfinal(monkeypatch):
    path_calls = _patch_penny_advancing_handshake(monkeypatch)

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert reason == "ok"
    assert candidate is not None
    assert candidate["price"] == pytest.approx(101.0)
    assert candidate["final_market_path_round_count"] == 2
    assert len(path_calls) == api._STOCK_FINAL_MAX_PATH_ROUNDS


def test_penny_revalidation_blocks_stop_touch_in_incremental_handshake_path(
    monkeypatch,
):
    _patch_penny_advancing_handshake(monkeypatch)
    path_calls = []

    def _market_path(_ticker, observed_ts, **kwargs):
        path_calls.append(observed_ts)
        is_incremental = len(path_calls) == 2
        return {
            "ok": True,
            "bars": [{
                "timestamp": observed_ts,
                "high": 104.0,
                "low": 94.0 if is_incremental else 96.0,
            }],
            "source": "unit_path",
            "first_timestamp": observed_ts,
            "last_timestamp": observed_ts,
            "coverage_verified": True,
            "coverage_start_timestamp": observed_ts,
            "coverage_end_timestamp": kwargs["now_ts"],
        }

    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _market_path)

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert candidate is None
    assert reason == "final_stop_touched_since_trigger"
    assert len(path_calls) == api._STOCK_FINAL_MAX_PATH_ROUNDS


@pytest.mark.parametrize(
    ("final_watermark", "expected_reason"),
    [
        (
            "unbounded",
            "final_market_path_handshake_unbounded_trade_advance",
        ),
        ("missing", "final_handshake_last_trade_timestamp_missing"),
    ],
)
def test_penny_revalidation_blocks_invalid_qfinal_watermark(
    monkeypatch, final_watermark, expected_reason
):
    path_calls = _patch_penny_advancing_handshake(
        monkeypatch, final_watermark=final_watermark
    )

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert candidate is None
    assert reason == expected_reason
    assert len(path_calls) == api._STOCK_FINAL_MAX_PATH_ROUNDS


def test_penny_revalidation_blocks_q2_without_last_trade_watermark(monkeypatch):
    source_ts = NOW - 60
    _patch_penny_candidate(
        monkeypatch,
        trigger_ts=source_ts - 300,
        path_bars=[{"timestamp": source_ts, "high": 104.0, "low": 96.0}],
    )
    quotes = iter([
        {
            "bid": 99.8,
            "ask": 100.0,
            "spread_bps": 20.0,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 61,
        },
        {
            "bid": 100.3,
            "ask": 100.5,
            "spread_bps": 20.0,
            "observed_ts": NOW - 5,
            "receipt_ts": NOW,
            "last_trade_ts": None,
        },
    ])
    monkeypatch.setattr(
        api, "_penny_fetch_live_spread", lambda *_a, **_k: next(quotes)
    )

    candidate, reason = api._penny_revalidate_buy_candidate(
        _penny_row(), now_ts=NOW
    )

    assert candidate is None
    assert reason == "final_handshake_last_trade_timestamp_missing"


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
