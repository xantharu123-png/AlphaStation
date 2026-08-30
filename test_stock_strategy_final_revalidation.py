"""Regression tests for the final stock-strategy market-path guard."""

from datetime import datetime, timezone

import pytest

import api


NOW = datetime(2026, 8, 13, 14, 0, tzinfo=timezone.utc).timestamp()


def _row(direction="LONG", **updates):
    if direction == "SHORT":
        setup = {
            "direction": "SHORT",
            "entry": 100.0,
            "stop": 105.0,
            "tp1": 90.0,
            "tp2": 80.0,
        }
    else:
        setup = {
            "direction": "LONG",
            "entry": 100.0,
            "stop": 95.0,
            "tp1": 110.0,
            "tp2": 120.0,
        }
    row = {
        "Ticker": "AAA",
        "ticker": "AAA",
        "Strategy": "Gap Momentum Short" if direction == "SHORT" else "Momentum Breakout Long",
        "Signal_Direction": direction,
        "grade": "A",
        "score": 91,
        "RVOL": 2.5,
        "Preis": 100.0,
        "price": 100.0,
        "Prev_Close": 98.0,
        "change_pct": 2.04,
        "entry_quality": "SWING_SETUP",
        "trade_setup": setup,
        "scan_price_observed_at": datetime.fromtimestamp(NOW - 600, timezone.utc).isoformat(),
        "scan_price_source": "polygon_snapshot",
    }
    row.update(updates)
    return row


def _patch_market_evidence(monkeypatch, *, direction="LONG", bars=None):
    if bars is None:
        bars = [
            {"timestamp": NOW - 600, "high": 104.0, "low": 96.0},
            {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
        ]
    else:
        bars = list(bars)
        if not any(bar["timestamp"] == NOW - 600 for bar in bars):
            bars.append({"timestamp": NOW - 600, "high": 104.0, "low": 96.0})
        if not any(bar["timestamp"] == NOW - 60 for bar in bars):
            bars.append({"timestamp": NOW - 60, "high": 104.0, "low": 96.0})
        bars.sort(key=lambda bar: bar["timestamp"])
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": bars,
            "source": "polygon_1m_aggs",
            "first_timestamp": bars[0]["timestamp"],
            "last_timestamp": bars[-1]["timestamp"],
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "age_seconds": 10.0,
            "source": api._STOCK_FINAL_PRICE_SOURCE,
            "last_trade_ts": NOW - 20,
        },
    )


def test_final_revalidation_long_uses_fresh_ask_without_pre_delivery_fill(monkeypatch):
    _patch_market_evidence(monkeypatch)

    result = api._revalidate_stock_strategy_mail_candidate(
        _row("LONG"), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result["ok"] is True
    item = result["candidate"]
    assert item["price"] == pytest.approx(100.2)
    assert item["Preis"] == pytest.approx(100.2)
    assert item["fill_evidence_verified"] is False
    assert item["quote_evidence_verified"] is True
    assert item["fill_evidence_timing"] == "pre_delivery_quote"
    assert item["price_source"] == "polygon_snapshot_revalidated"
    assert item["price_mode"] == "ask"
    assert item["price_session"] == "US_REGULAR"
    assert item["price_observed_at"] == "2026-08-13T13:59:50+00:00"
    assert item["trade_setup"]["live_price"] == pytest.approx(100.2)
    assert item["final_market_path_bars"] == 2
    assert item["final_quote_spread_pct"] == pytest.approx(0.4)


def test_final_revalidation_carries_exceeded_reachability_budget_without_blocking(monkeypatch):
    _patch_market_evidence(monkeypatch)
    row = _row(
        trade_horizon="swing",
        target_reachability_atr_budgets={"swing": 1.0},
    )
    row["trade_setup"]["atr"] = 1.0

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=NOW, price_session="US_REGULAR"
    )

    assert result["ok"] is True
    telemetry = result["candidate"]["target_reachability"]
    assert telemetry["data_available"] is True
    assert telemetry["within_budget"] is False
    assert telemetry["issues"] == ["target_beyond_configured_atr_budget"]
    assert result["candidate"]["trade_setup"]["target_reachability"] == telemetry


def test_final_revalidation_short_is_symmetric_and_uses_bid(monkeypatch):
    _patch_market_evidence(
        monkeypatch,
        direction="SHORT",
        bars=[{"timestamp": NOW - 300, "high": 103.0, "low": 94.0}],
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row("SHORT"), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result["ok"] is True
    item = result["candidate"]
    assert item["price"] == pytest.approx(99.8)
    assert item["price_mode"] == "bid"
    assert item["fill_evidence_verified"] is False
    assert item["price_source"] == "polygon_snapshot_revalidated"


def test_completed_polygon_bar_is_valid_causal_scan_source(monkeypatch):
    _patch_market_evidence(monkeypatch)
    row = _row(scan_price_source="polygon_completed_5m_orb_bar")

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=NOW, price_session="US_REGULAR", scanner_name="orb"
    )

    assert result["ok"] is True


@pytest.mark.parametrize("scanner_name", ["stock_strategy", "orb"])
def test_stock_and_orb_accept_provider_empty_aggregate_minutes(
    monkeypatch, scanner_name
):
    _patch_market_evidence(monkeypatch)
    scan_ts = NOW - 600
    quote_ts = NOW - 10
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": scan_ts, "high": 104.0, "low": 96.0},
                {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
            ],
            "source": "polygon_1m_aggs_with_provider_empty_intervals",
            "first_timestamp": scan_ts,
            "last_timestamp": NOW - 60,
            "coverage_verified": True,
            "coverage_start_timestamp": scan_ts,
            "coverage_end_timestamp": quote_ts,
            "provider_empty_aggregate_interval_count": 1,
            "provider_empty_aggregate_intervals": [
                {
                    "start_timestamp": scan_ts + 60,
                    "end_timestamp": NOW - 60,
                    "proof": "massive_no_qualifying_trade_aggregate",
                }
            ],
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(),
        now_ts=NOW,
        price_session="US_REGULAR",
        scanner_name=scanner_name,
    )

    assert result["ok"] is True
    candidate = result["candidate"]
    assert candidate["final_market_path_bars"] == 2
    assert candidate["final_market_path_provider_empty_intervals"] == 1
    assert candidate["final_market_path_source"].endswith(
        "provider_empty_intervals"
    )


@pytest.mark.parametrize(
    ("direction", "bars", "reason"),
    [
        ("LONG", [{"timestamp": NOW - 300, "high": 104.0, "low": 94.9}], "final_stop_touched_since_scan"),
        ("LONG", [{"timestamp": NOW - 300, "high": 110.1, "low": 99.0}], "final_tp1_touched_since_scan"),
        ("SHORT", [{"timestamp": NOW - 300, "high": 105.1, "low": 99.0}], "final_stop_touched_since_scan"),
        ("SHORT", [{"timestamp": NOW - 300, "high": 101.0, "low": 89.9}], "final_tp1_touched_since_scan"),
    ],
)
def test_final_revalidation_blocks_any_stop_or_tp1_touch_since_scan(
    monkeypatch, direction, bars, reason
):
    _patch_market_evidence(monkeypatch, direction=direction, bars=bars)

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(direction), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": reason}


def test_final_revalidation_blocks_ambiguous_same_bar_stop_and_tp1(monkeypatch):
    _patch_market_evidence(
        monkeypatch,
        bars=[{"timestamp": NOW - 300, "high": 111.0, "low": 94.0}],
    )

    result = api._revalidate_stock_strategy_mail_candidate(_row(), now_ts=NOW)

    assert result == {
        "ok": False,
        "reason": "final_stop_and_tp1_touched_since_scan",
    }


def test_final_revalidation_blocks_current_minute_tp_touch_and_rebound(monkeypatch):
    quote_ts = NOW - 10

    class AggregateResponse:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 600) * 1000), "h": 104.0, "l": 96.0},
                    {"t": int((NOW - 60) * 1000), "h": 110.5, "l": 99.0},
                ],
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")

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

    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda url, *args, **kwargs: (
            TradeResponse() if "/v3/trades/" in url else AggregateResponse()
        ),
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote_ts,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 61,
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {
        "ok": False,
        "reason": "final_tp1_touched_since_scan",
    }


def test_final_revalidation_uses_complete_raw_trades_for_lagging_current_bar(
    monkeypatch,
):
    quote_ts = NOW - 10
    snapshot_last_trade = quote_ts - 1
    calls = []

    class Response:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    aggregate_payload = {
        "status": "OK",
        "results": [
            {"t": int((NOW - 600) * 1000), "h": 104.0, "l": 96.0},
            # Harmless but lagging current-minute aggregate.
            {"t": int((NOW - 60) * 1000), "h": 104.0, "l": 99.0},
        ],
    }
    trade_payload = {
        "status": "OK",
        "results": [
            {
                "sip_timestamp": int((quote_ts - 20) * 1_000_000_000),
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
        calls.append(url)
        return Response(
            trade_payload if "/v3/trades/" in url else aggregate_payload
        )

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
            "last_trade_ts": snapshot_last_trade,
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {
        "ok": False,
        "reason": "final_stop_touched_since_scan",
    }
    assert len([url for url in calls if "/v3/trades/" in url]) == 1


@pytest.mark.parametrize(
    ("first_timestamp", "last_timestamp", "reason"),
    [
        (NOW - 540, NOW - 60, "final_market_path_start_gap"),
        (NOW - 600, NOW - 90, "final_market_path_end_gap"),
    ],
)
def test_final_revalidation_rejects_truncated_market_path_edges(
    monkeypatch, first_timestamp, last_timestamp, reason
):
    _patch_market_evidence(monkeypatch)
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": first_timestamp, "high": 104.0, "low": 96.0},
                {"timestamp": last_timestamp, "high": 104.0, "low": 96.0},
            ],
            "source": "polygon_1m_aggs",
            "first_timestamp": first_timestamp,
            "last_timestamp": last_timestamp,
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": reason}


def test_final_revalidation_blocks_wide_live_spread(monkeypatch):
    _patch_market_evidence(monkeypatch)
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 98.0,
            "ask": 102.1,
            "observed_ts": NOW - 10,
            "age_seconds": 10.0,
            "source": api._STOCK_FINAL_PRICE_SOURCE,
            "last_trade_ts": NOW - 20,
        },
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_quote_spread_too_wide"}


def test_final_revalidation_requires_executable_session(monkeypatch):
    _patch_market_evidence(monkeypatch)

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="UNKNOWN"
    )

    assert result == {"ok": False, "reason": "final_price_session_not_executable"}


def test_regular_open_rejects_quote_timestamped_in_premarket(monkeypatch):
    receipt = datetime(2026, 8, 13, 13, 30, 10, tzinfo=timezone.utc).timestamp()
    scan = receipt - 600
    quote = receipt - 20  # 09:29:50 America/New_York: still premarket.
    first_bar = scan - 10
    last_bar = quote - 50
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": first_bar, "high": 104.0, "low": 96.0},
                {"timestamp": last_bar, "high": 104.0, "low": 96.0},
            ],
            "source": "polygon_1m_aggs",
            "first_timestamp": first_bar,
            "last_timestamp": last_bar,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote,
            "age_seconds": 20.0,
            "source": api._STOCK_FINAL_PRICE_SOURCE,
            "last_trade_ts": quote - 1,
        },
    )
    row = _row(
        scan_price_observed_at=datetime.fromtimestamp(scan, timezone.utc).isoformat()
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=receipt, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_quote_session_mismatch"}


def test_quote_session_respects_us_holiday_and_early_close():
    holiday = datetime(2026, 7, 3, 14, 0, tzinfo=timezone.utc).timestamp()
    after_early_close = datetime(2026, 7, 2, 17, 30, tzinfo=timezone.utc).timestamp()

    assert api._stock_quote_session_at(holiday) == "CLOSED"
    assert api._stock_quote_session_at(after_early_close) == "POSTMARKET"


def test_regular_close_rejects_quote_received_after_market_close(monkeypatch):
    receipt = datetime(2026, 8, 13, 20, 0, 10, tzinfo=timezone.utc).timestamp()
    quote = datetime(2026, 8, 13, 19, 59, 50, tzinfo=timezone.utc).timestamp()
    scan = quote - 600
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": scan, "high": 104.0, "low": 96.0},
                {"timestamp": quote - 50, "high": 104.0, "low": 96.0},
            ],
            "first_timestamp": scan,
            "last_timestamp": quote - 50,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote,
            "receipt_ts": receipt,
            "last_trade_ts": quote - 1,
        },
    )
    row = _row(
        scan_price_observed_at=datetime.fromtimestamp(scan, timezone.utc).isoformat()
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=receipt, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_receipt_session_mismatch"}


@pytest.mark.parametrize("scanner_name", ["stock_strategy", "orb"])
def test_final_revalidation_fetches_quote_before_watermarked_path(
    monkeypatch, scanner_name
):
    calls = []
    quote_ts = NOW - 10
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: calls.append("quote") or {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": quote_ts,
            "age_seconds": 10.0,
            "source": api._STOCK_FINAL_PRICE_SOURCE,
            "last_trade_ts": quote_ts - 15,
        },
    )

    def _path(*args, **kwargs):
        calls.append(
            ("path", kwargs.get("now_ts"), kwargs.get("last_trade_ts"))
        )
        return {
            "ok": True,
            "bars": [
                {"timestamp": NOW - 600, "high": 104.0, "low": 96.0},
                {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
            ],
            "source": "polygon_1m_aggs",
            "first_timestamp": NOW - 600,
            "last_timestamp": NOW - 60,
        }

    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(),
        now_ts=NOW,
        price_session="US_REGULAR",
        scanner_name=scanner_name,
    )

    assert result["ok"] is True
    assert calls == ["quote", ("path", quote_ts, quote_ts - 15), "quote"]


def test_final_revalidation_uses_q2_entry_price_not_q1(monkeypatch):
    q1_ts = NOW - 10
    q2_ts = NOW - 5
    snapshots = iter([
        {
            "ok": True,
            "bid": 99.8,
            "ask": 100.0,
            "observed_ts": q1_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q1_ts - 1,
        },
        {
            "ok": True,
            "bid": 101.8,
            "ask": 102.0,
            "observed_ts": q2_ts,
            "receipt_ts": NOW,
            # No trade advanced beyond the already proven Q1 path.
            "last_trade_ts": q1_ts - 1,
        },
    ])
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [
                {"timestamp": NOW - 600, "high": 104.0, "low": 96.0},
                {"timestamp": NOW - 60, "high": 104.0, "low": 96.0},
            ],
            "source": "unit_path",
            "first_timestamp": NOW - 600,
            "last_timestamp": NOW - 60,
            "coverage_verified": True,
            "coverage_start_timestamp": NOW - 600,
            "coverage_end_timestamp": q1_ts,
        },
    )
    monkeypatch.setattr(
        api,
        "validate_fill_quality",
        lambda *args, **kwargs: {"valid": True},
    )
    row = _row()
    row["trade_setup"].update({"tp1": 113.0, "tp2": 123.0})

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=NOW, price_session="US_REGULAR"
    )

    assert result["ok"] is True
    assert result["candidate"]["price"] == pytest.approx(102.0)
    assert result["candidate"]["final_quote_ask"] == pytest.approx(102.0)
    assert result["candidate"]["price_observed_at"] == datetime.fromtimestamp(
        q2_ts, timezone.utc
    ).isoformat()
    assert result["candidate"]["final_market_path_round_count"] == 1


def _patch_advancing_quote_handshake(monkeypatch, *, unbounded=False):
    q1_ts = NOW - 20
    q2_ts = NOW - 10
    q3_ts = NOW - 5
    q2_trade_ts = NOW - 11
    q3_trade_ts = NOW - 6 if unbounded else q2_trade_ts
    snapshots = iter([
        {
            "ok": True,
            "bid": 99.8,
            "ask": 100.0,
            "observed_ts": q1_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q1_ts - 1,
        },
        {
            "ok": True,
            "bid": 100.8,
            "ask": 101.0,
            "observed_ts": q2_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q2_trade_ts,
        },
        {
            "ok": True,
            "bid": 101.8,
            "ask": 102.0,
            "observed_ts": q3_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q3_trade_ts,
        },
    ])
    path_calls = []

    def _path(_ticker, observed_ts, **kwargs):
        path_calls.append((observed_ts, kwargs["now_ts"], kwargs["last_trade_ts"]))
        return {
            "ok": True,
            "bars": [
                {
                    "timestamp": observed_ts,
                    "high": 104.0,
                    "low": 96.0,
                }
            ],
            "source": "unit_path",
            "first_timestamp": observed_ts,
            "last_timestamp": observed_ts,
            "coverage_verified": True,
            "coverage_start_timestamp": observed_ts,
            "coverage_end_timestamp": kwargs["now_ts"],
        }

    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)
    monkeypatch.setattr(
        api,
        "validate_fill_quality",
        lambda *args, **kwargs: {"valid": True},
    )
    return path_calls


def test_final_revalidation_incrementally_extends_path_when_q2_trade_advances(
    monkeypatch,
):
    path_calls = _patch_advancing_quote_handshake(monkeypatch)
    row = _row()
    row["trade_setup"].update({"tp1": 113.0, "tp2": 123.0})

    result = api._revalidate_stock_strategy_mail_candidate(
        row, now_ts=NOW, price_session="US_REGULAR"
    )

    assert result["ok"] is True
    assert result["candidate"]["price"] == pytest.approx(102.0)
    assert result["candidate"]["final_market_path_round_count"] == 2
    assert len(path_calls) == 2
    assert path_calls[1][0] == pytest.approx(NOW - 20)
    assert path_calls[1][1] == pytest.approx(NOW - 10)
    assert path_calls[1][2] == pytest.approx(NOW - 11)


def test_submillisecond_q2_trade_advance_is_replayed_and_stop_touch_blocks(
    monkeypatch,
):
    q1_ts = NOW - 20
    advanced_trade_ts = q1_ts + 0.0005
    snapshots = iter([
        {
            "ok": True, "bid": 99.8, "ask": 100.0,
            "observed_ts": q1_ts, "receipt_ts": NOW,
            "last_trade_ts": q1_ts - 1,
        },
        {
            "ok": True, "bid": 100.8, "ask": 101.0,
            "observed_ts": NOW - 10, "receipt_ts": NOW,
            "last_trade_ts": advanced_trade_ts,
        },
        {
            "ok": True, "bid": 100.8, "ask": 101.0,
            "observed_ts": NOW - 5, "receipt_ts": NOW,
            "last_trade_ts": advanced_trade_ts,
        },
    ])
    path_calls = []

    def _path(_ticker, observed_ts, **kwargs):
        path_calls.append((observed_ts, kwargs["now_ts"]))
        incremental = len(path_calls) == 2
        return {
            "ok": True,
            "bars": [{
                "timestamp": observed_ts,
                "high": 104.0,
                "low": 94.0 if incremental else 96.0,
            }],
            "source": "unit_path",
            "coverage_verified": True,
            "coverage_start_timestamp": observed_ts,
            "coverage_end_timestamp": kwargs["now_ts"],
        }

    monkeypatch.setattr(
        api, "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)
    monkeypatch.setattr(
        api, "validate_fill_quality", lambda *args, **kwargs: {"valid": True}
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {"ok": False, "reason": "final_stop_touched_since_scan"}
    assert len(path_calls) == 2
    assert path_calls[1][1] == pytest.approx(NOW - 10)


def test_final_revalidation_blocks_trade_advance_after_second_path_round(
    monkeypatch,
):
    path_calls = _patch_advancing_quote_handshake(monkeypatch, unbounded=True)

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {
        "ok": False,
        "reason": "final_market_path_handshake_unbounded_trade_advance",
    }
    assert len(path_calls) == api._STOCK_FINAL_MAX_PATH_ROUNDS


def test_final_revalidation_blocks_q2_without_last_trade_watermark(monkeypatch):
    _patch_market_evidence(monkeypatch)
    snapshots = iter([
        {
            "ok": True,
            "bid": 99.8,
            "ask": 100.0,
            "observed_ts": NOW - 10,
            "receipt_ts": NOW,
            "last_trade_ts": NOW - 20,
        },
        {
            "ok": True,
            "bid": 100.8,
            "ask": 101.0,
            "observed_ts": NOW - 5,
            "receipt_ts": NOW,
            "last_trade_ts": None,
        },
    ])
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {
        "ok": False,
        "reason": "final_handshake_last_trade_timestamp_missing",
    }


def test_final_revalidation_blocks_qfinal_without_last_trade_watermark(monkeypatch):
    _patch_advancing_quote_handshake(monkeypatch)
    q1_ts = NOW - 20
    q2_ts = NOW - 10
    snapshots = iter([
        {
            "ok": True,
            "bid": 99.8,
            "ask": 100.0,
            "observed_ts": q1_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q1_ts - 1,
        },
        {
            "ok": True,
            "bid": 100.8,
            "ask": 101.0,
            "observed_ts": q2_ts,
            "receipt_ts": NOW,
            "last_trade_ts": q2_ts - 1,
        },
        {
            "ok": True,
            "bid": 101.8,
            "ask": 102.0,
            "observed_ts": NOW - 5,
            "receipt_ts": NOW,
            "last_trade_ts": None,
        },
    ])
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )

    result = api._revalidate_stock_strategy_mail_candidate(
        _row(), now_ts=NOW, price_session="US_REGULAR"
    )

    assert result == {
        "ok": False,
        "reason": "final_handshake_last_trade_timestamp_missing",
    }


@pytest.mark.parametrize(
    ("updates", "reason"),
    [
        ({"scan_price_observed_at": None}, "final_scan_observation_missing"),
        ({"scan_price_source": None}, "final_scan_price_source_missing"),
        (
            {"scan_price_observed_at": datetime.fromtimestamp(NOW - 13 * 3600, timezone.utc).isoformat()},
            "final_scan_observation_stale",
        ),
    ],
)
def test_final_revalidation_fails_closed_without_recent_scan_evidence(updates, reason):
    result = api._revalidate_stock_strategy_mail_candidate(
        _row(**updates), now_ts=NOW
    )

    assert result == {"ok": False, "reason": reason}


def test_final_snapshot_requires_fresh_timestamped_executable_quote(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "ticker": {
                    "lastQuote": {
                        "p": 99.8,
                        "P": 100.2,
                        "t": int((NOW - 91) * 1_000_000_000),
                    }
                }
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_snapshot("AAA", now_ts=NOW)

    assert result["ok"] is False
    assert result["reason"] == "final_quote_stale"
    assert result["age_seconds"] > 90


def test_final_market_path_keeps_observation_minute_conservatively(monkeypatch):
    observation = NOW - 31

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "status": "OK",
                "results": [
                    {"t": int((NOW - 60) * 1000), "h": 110.5, "l": 99.0},
                ]
            }

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())

    result = api._fetch_stock_revalidation_market_path(
        "AAA", observation, now_ts=NOW
    )

    assert result["ok"] is True
    assert len(result["bars"]) == 1
    assert result["bars"][0]["high"] == pytest.approx(110.5)


def _patch_strategy_mail_pipeline(monkeypatch):
    sent = []
    tracked = []
    events = []
    released = []
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"AAA", "BBB"}, "unit"))
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **k: dict(row))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": True, "session": "US_REGULAR", "reason": "unit"})
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda scanner, row, *a, **k: dict(row))
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", lambda *a, **k: (True, ""))
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda scanner, row, now=None: {
        "alertable_now": True,
        "suppression_reasons": [],
        "cooldown_key": f"stock_strategy_{row['ticker']}",
        "ticker": row["ticker"],
        "grade": row["grade"],
        "score": row["score"],
        "price": row["price"],
        "rvol": row["RVOL"],
    })
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **k: True)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda key, **k: released.append(key) or True)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda *a, **k: True)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append({"subject": subject, "body": body, **kwargs}) or True)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda scanner, rows, **kwargs: tracked.append((scanner, rows, kwargs)))
    monkeypatch.setattr(api, "_record_email_event", lambda subject, status, reason="": events.append((subject, status, reason)))
    return sent, tracked, events, released


def test_strategy_mail_and_tracker_receive_only_revalidated_price(monkeypatch):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    _patch_market_evidence(monkeypatch)
    monkeypatch.setattr(api.time, "time", lambda: NOW)
    api._EMAIL_COOLDOWN.clear()

    api._send_strategy_scan_alerts("Momentum Breakout Long", [_row()], "stocks")

    assert len(sent) == 1
    assert "100.20" in sent[0]["body"]
    assert tracked == []
    assert sent[0]["tracking_scanner"] == "stock_strategy"
    tracked_row = sent[0]["tracking_rows"][0]
    assert tracked_row["price"] == pytest.approx(100.2)
    assert tracked_row["fill_evidence_verified"] is False
    assert tracked_row["fill_evidence_timing"] == "pre_delivery_quote"
    assert tracked_row["price_source"] == "polygon_snapshot_revalidated"
    assert tracked_row["price_observed_at"] == "2026-08-13T13:59:50+00:00"
    assert released == []


def test_strategy_revalidation_rejection_releases_claim_and_records_reason(monkeypatch):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *a, **k: {
            "ok": True,
            "bid": 99.8,
            "ask": 100.2,
            "observed_ts": NOW - 10,
            "age_seconds": 10.0,
            "source": api._STOCK_FINAL_PRICE_SOURCE,
        },
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *a, **k: {"ok": False, "reason": "final_market_path_missing"},
    )
    monkeypatch.setattr(api.time, "time", lambda: NOW)
    api._EMAIL_COOLDOWN.clear()

    api._send_strategy_scan_alerts("Momentum Breakout Long", [_row()], "stocks")

    assert sent == []
    assert tracked == []
    assert released == ["stock_strategy_AAA"]
    assert any(
        status == "skipped" and "final_market_path_missing" in reason
        for _, status, reason in events
    )


def test_each_stock_row_is_revalidated_immediately_before_its_own_send(
    monkeypatch,
):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    api._EMAIL_COOLDOWN.clear()
    rows = [
        _row(ticker="AAA", Ticker="AAA", score=89),
        _row(ticker="BBB", Ticker="BBB", score=95),
    ]
    calls = []

    def _validate(row, **kwargs):
        ticker = row["ticker"]
        calls.append(("validate", ticker))
        if ticker == "AAA":
            return {"ok": False, "reason": "final_stop_touched_since_scan"}
        candidate = dict(row)
        candidate.update({
            "price": 100.2,
            "Preis": 100.2,
            "fill_evidence_verified": False,
            "fill_evidence_timing": "pre_delivery_quote",
            "price_source": api._STOCK_FINAL_PRICE_SOURCE,
            "price_observed_at": "2026-08-13T13:59:50+00:00",
        })
        return {"ok": True, "candidate": candidate}

    def _send(subject, body, **kwargs):
        ticker = kwargs["tracking_rows"][0]["ticker"]
        calls.append(("send", ticker))
        sent.append({"subject": subject, "body": body, **kwargs})
        return True

    monkeypatch.setattr(
        api, "_revalidate_stock_strategy_mail_candidate", _validate
    )
    monkeypatch.setattr(api, "_send_email_alert", _send)

    api._send_strategy_scan_alerts("Momentum Breakout Long", rows, "stocks")

    assert calls == [
        ("validate", "BBB"),
        ("send", "BBB"),
        ("validate", "AAA"),
    ]
    assert [mail["tracking_rows"][0]["ticker"] for mail in sent] == ["BBB"]
    assert released == ["stock_strategy_AAA"]
    assert any(
        "mail_adjacent_stock_revalidation:final_stop_touched_since_scan"
        in reason
        for _, _, reason in events
    )


def test_regime_filtered_stock_row_uses_no_provider_revalidation(monkeypatch):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    api._EMAIL_COOLDOWN.clear()
    validation_calls = []
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: validation_calls.append(row["ticker"])
        or {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(
        api,
        "_regime_mail_decision",
        lambda *args, **kwargs: {
            "state": "YELLOW",
            "layer": "market",
            "reason_tag": "market_regime_yellow",
            "banner": "",
            "score_boost": 20,
            "max_rows": 2,
        },
    )

    api._send_strategy_scan_alerts(
        "Momentum Breakout Long", [_row(score=91)], "stocks"
    )

    assert validation_calls == []
    assert sent == []
    assert released == ["stock_strategy_AAA"]
    assert any(
        status == "skipped" and reason == "market_regime_yellow_filtered_all"
        for _, status, reason in events
    )


def test_open_equivalent_stock_row_uses_no_provider_revalidation(monkeypatch):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    api._EMAIL_COOLDOWN.clear()
    validation_calls = []
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: validation_calls.append(row["ticker"])
        or {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(
        api, "_has_open_equivalent_trade_safe", lambda *args, **kwargs: True
    )

    api._send_strategy_scan_alerts("Momentum Breakout Long", [_row()], "stocks")

    assert validation_calls == []
    assert sent == []
    assert released == ["stock_strategy_AAA"]
    assert any(
        status == "skipped" and reason == "open_equivalent_trade:1"
        for _, status, reason in events
    )


def test_single_wire_exception_releases_all_unattempted_row_claims(monkeypatch):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    api._EMAIL_COOLDOWN.clear()
    rows = [_row(ticker="AAA", Ticker="AAA"), _row(ticker="BBB", Ticker="BBB")]
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("send boom")),
    )

    with pytest.raises(RuntimeError, match="send boom"):
        api._send_strategy_scan_alerts("Momentum Breakout Long", rows, "stocks")

    assert sorted(released) == ["stock_strategy_AAA", "stock_strategy_BBB"]


def test_signal_performance_defaults_to_mature_cohort_and_forwards_override(monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_require_admin", lambda authorization: None)
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda **kwargs: calls.append(kwargs) or {"ok": True, **kwargs},
    )

    mature = api.api_signal_performance(days=30, mature_only=True, authorization="unit")
    diagnostic = api.api_signal_performance(days=7, mature_only=False, authorization="unit")

    assert mature["mature_only"] is True
    assert diagnostic["mature_only"] is False
    assert calls == [
        {"days": 30, "mature_only": True},
        {"days": 7, "mature_only": False},
    ]
