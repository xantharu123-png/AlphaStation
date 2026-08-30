import copy
import threading
import time

import pytest

import api


@pytest.fixture(autouse=True)
def _restore_quote_capability_state():
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        original_state = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
        original_sequence = api._STOCK_QUOTE_CAPABILITY_PROBE_SEQUENCE
        original_composite_sequence = (
            api._STOCK_QUOTE_CAPABILITY_COMPOSITE_SEQUENCE
        )
    yield
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        api._STOCK_QUOTE_CAPABILITY.clear()
        api._STOCK_QUOTE_CAPABILITY.update(original_state)
        api._STOCK_QUOTE_CAPABILITY_PROBE_SEQUENCE = original_sequence
        api._STOCK_QUOTE_CAPABILITY_COMPOSITE_SEQUENCE = (
            original_composite_sequence
        )


def _snapshot_payload(now_ts, *, include_last_trade=False):
    ticker = {
        "lastQuote": {
            "p": 199.9,
            "P": 200.1,
            "t": int((now_ts - 5.0) * 1_000_000_000),
        }
    }
    if include_last_trade:
        ticker["lastTrade"] = {
            "p": 200.0,
            "t": int((now_ts - 8.0) * 1_000_000_000),
        }
    return {"ticker": ticker}


class _Response:
    def __init__(self, status_code, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_closed_market_is_not_applicable_not_failed_entitlement(monkeypatch):
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        prior_probe_checked_at = api._STOCK_QUOTE_CAPABILITY.get("checked_at")
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "CLOSED")
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_: {
            "allowed": False,
            "session": "CLOSED",
            "reason": "unit-test weekend",
        },
    )

    capability, public_value = api._stock_quote_capability_health_snapshot()

    assert public_value is None
    assert capability["status"] == "market_closed"
    assert capability["applicable"] is False
    assert capability["realtime_quote"]["status"] == "not_applicable"
    assert "unit-test weekend" in capability["reason"]
    assert capability["checked_at"] == prior_probe_checked_at
    assert capability["assessment_checked_at"]


def test_http_403_reports_endpoint_specific_uncertain_plan_diagnosis(monkeypatch):
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(403),
    )

    result = api._fetch_stock_revalidation_snapshot("AAPL")

    assert result == {"ok": False, "reason": "final_snapshot_http_403"}
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        capability = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert capability["endpoint"]["name"] == api._STOCK_QUOTE_CAPABILITY_ENDPOINT
    assert capability["endpoint"]["access"] == "forbidden"
    assert capability["endpoint"]["http_status"] == 403
    assert capability["provider_entitlement"] == "forbidden_or_plan_restricted"
    assert "ticker" not in capability["latest_probe"]
    assert capability["latest_probe"]["checked_at"]


def test_stale_200_confirms_endpoint_access_but_not_realtime_or_plan(monkeypatch):
    now_ts = time.time()
    payload = _snapshot_payload(now_ts - 600.0)
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(200, payload),
    )

    result = api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)

    assert result["ok"] is False
    assert result["reason"] == "final_quote_stale"
    assert result["age_seconds"] > 90
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        latest = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY["latest_probe"])
    assert latest["endpoint_access"] == "accessible"
    assert latest["provider_entitlement"] == "endpoint_access_confirmed"
    assert latest["quote_status"] == "stale_quote_observed"
    assert latest["quote_age_seconds"] >= 600


def test_newer_started_probe_cannot_be_overwritten_by_slow_old_probe(monkeypatch):
    now_ts = time.time()
    old_started = threading.Event()
    release_old = threading.Event()

    def _get(url, **kwargs):
        if url.endswith("/AAPL"):
            old_started.set()
            assert release_old.wait(timeout=3)
            return _Response(200, _snapshot_payload(now_ts))
        return _Response(403)

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", _get)
    old_result = []
    thread = threading.Thread(
        target=lambda: old_result.append(
            api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)
        )
    )
    thread.start()
    assert old_started.wait(timeout=3)

    newer = api._fetch_stock_revalidation_snapshot("MSFT", now_ts=now_ts)
    release_old.set()
    thread.join(timeout=3)

    assert newer["reason"] == "final_snapshot_http_403"
    assert old_result and old_result[0]["ok"] is True
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        capability = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert capability["latest_probe"]["http_status"] == 403
    assert "ticker" not in capability["latest_probe"]
    assert "last_verified_ticker" not in capability


def test_slow_old_probe_cannot_publish_while_newer_probe_is_in_flight(monkeypatch):
    now_ts = time.time()
    old_started = threading.Event()
    new_started = threading.Event()
    release_old = threading.Event()
    release_new = threading.Event()

    def _get(url, **kwargs):
        if url.endswith("/AAPL"):
            old_started.set()
            assert release_old.wait(timeout=3)
            return _Response(200, _snapshot_payload(now_ts))
        new_started.set()
        assert release_new.wait(timeout=3)
        return _Response(403)

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "rate_limited_get", _get)
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        api._STOCK_QUOTE_CAPABILITY["latest_probe"] = None
        api._STOCK_QUOTE_CAPABILITY["last_verified_realtime_quote_at"] = None
        api._STOCK_QUOTE_CAPABILITY.pop("last_verified_quote_observed_at", None)

    old_result = []
    new_result = []
    old_thread = threading.Thread(
        target=lambda: old_result.append(
            api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)
        )
    )
    new_thread = threading.Thread(
        target=lambda: new_result.append(
            api._fetch_stock_revalidation_snapshot("MSFT", now_ts=now_ts)
        )
    )
    old_thread.start()
    assert old_started.wait(timeout=3)
    new_thread.start()
    assert new_started.wait(timeout=3)

    release_old.set()
    old_thread.join(timeout=3)
    assert old_result and old_result[0]["ok"] is True
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        interim = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert interim.get("latest_probe") is None
    assert interim.get("last_verified_realtime_quote_at") is None

    release_new.set()
    new_thread.join(timeout=3)
    assert new_result and new_result[0]["reason"] == "final_snapshot_http_403"
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        final = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert final["latest_probe"]["http_status"] == 403


def test_newer_hard_endpoint_failure_invalidates_older_fresh_verification(
    monkeypatch,
):
    now_ts = time.time()
    responses = iter([
        _Response(200, _snapshot_payload(now_ts)),
        _Response(403),
    ])
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: next(responses),
    )
    assert api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)["ok"]
    assert api._fetch_stock_revalidation_snapshot("MSFT", now_ts=now_ts) == {
        "ok": False,
        "reason": "final_snapshot_http_403",
    }
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_: {
            "allowed": True,
            "session": "US_REGULAR",
            "reason": "unit-test open",
        },
    )

    capability, public_value = api._stock_quote_capability_health_snapshot()

    assert public_value is False
    assert capability["status"] == "diagnostic_failed"
    assert capability["endpoint"]["access"] == "forbidden"


def test_snapshot_exposes_optional_last_trade_watermark_without_using_it_as_quote(
    monkeypatch,
):
    now_ts = time.time()
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(
            200,
            _snapshot_payload(now_ts, include_last_trade=True),
        ),
    )

    result = api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)

    assert result["ok"] is True
    assert result["bid"] == pytest.approx(199.9)
    assert result["ask"] == pytest.approx(200.1)
    assert result["last_trade_price"] == pytest.approx(200.0)
    assert result["last_trade_ts"] == pytest.approx(now_ts - 8.0, abs=0.001)


def test_snapshot_blocks_last_trade_later_than_quote_watermark(monkeypatch):
    now_ts = time.time()
    payload = {
        "ticker": {
            "lastQuote": {
                "p": 199.9,
                "P": 200.1,
                "t": int((now_ts - 5.0) * 1_000_000_000),
            },
            "lastTrade": {
                "p": 200.0,
                "t": int((now_ts - 1.0) * 1_000_000_000),
            },
        }
    }
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(200, payload),
    )

    result = api._fetch_stock_revalidation_snapshot("AAPL", now_ts=now_ts)

    assert result == {
        "ok": False,
        "reason": "final_last_trade_after_quote_timestamp",
    }


def test_scheduler_control_probe_skips_closed_market_without_provider_call(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "CLOSED")
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    result = api._stock_quote_capability_control_probe()

    assert result["status"] == "not_applicable"
    assert calls == []


def test_scheduler_control_probe_uses_one_aapl_snapshot_during_regular_session(
    monkeypatch,
):
    calls = []
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")

    def _fetch(ticker, **kwargs):
        calls.append((ticker, kwargs))
        with api._STOCK_QUOTE_CAPABILITY_LOCK:
            api._STOCK_QUOTE_CAPABILITY["latest_probe"] = {
                "probe_id": 777,
                "endpoint_access": "accessible",
                "reason": None,
            }
        return {
            "ok": True,
            "age_seconds": 4.0,
            "observed_ts": time.time() - 2,
            "last_trade_ts": time.time() - 3,
            "_capability_probe_id": 777,
        }

    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", _fetch)
    def _aggregate_probe(ticker, snapshot, **kwargs):
        checked_at = api.datetime.now(api.timezone.utc).isoformat()
        with api._STOCK_QUOTE_CAPABILITY_LOCK:
            api._STOCK_QUOTE_CAPABILITY["aggregate_endpoint"] = {
                "access": "accessible", "reason": None, "http_status": 200,
                "composite_generation": kwargs["composite_generation"],
                "market_session": kwargs["market_session"],
                "quote_probe_id": kwargs["quote_probe_id"],
            }
            api._STOCK_QUOTE_CAPABILITY.update({
                "last_verified_aggregate_at": checked_at,
                "last_verified_aggregate_generation": kwargs["composite_generation"],
                "last_verified_aggregate_session": kwargs["market_session"],
            })
        return {"ok": True, "status": "verified_bounded_1m_aggregates"}

    monkeypatch.setattr(api, "_stock_aggregate_capability_probe", _aggregate_probe)
    def _trade_probe(ticker, snapshot, **kwargs):
        with api._STOCK_QUOTE_CAPABILITY_LOCK:
            api._STOCK_QUOTE_CAPABILITY["trade_replay_endpoint"] = {
                "name": api._STOCK_TRADE_REPLAY_CAPABILITY_ENDPOINT,
                "path": api._STOCK_TRADE_REPLAY_CAPABILITY_PATH,
                "access": "accessible",
                "checked_at": api.datetime.now(api.timezone.utc).isoformat(),
                "reason": None,
                "http_status": 200,
                "composite_generation": kwargs["composite_generation"],
                "market_session": kwargs["market_session"],
                "quote_probe_id": kwargs["quote_probe_id"],
            }
            api._STOCK_QUOTE_CAPABILITY.update({
                "last_verified_trade_replay_at": api.datetime.now(
                    api.timezone.utc
                ).isoformat(),
                "last_verified_trade_replay_generation": kwargs[
                    "composite_generation"
                ],
                "last_verified_trade_replay_session": kwargs[
                    "market_session"
                ],
            })
        return {
            "ok": True,
            "status": "verified_bounded_trade_replay",
        }

    monkeypatch.setattr(
        api,
        "_stock_trade_replay_capability_probe",
        _trade_probe,
    )

    result = api._stock_quote_capability_control_probe()

    assert result["ok"] is True
    assert result["status"] == "verified_realtime_quote"
    assert result["ticker"] == "AAPL"
    assert result["age_seconds"] == 4.0
    assert result["trade_replay_status"] == "verified_bounded_trade_replay"
    assert result["aggregate_status"] == "verified_bounded_1m_aggregates"
    assert result["generation"] > 0
    assert len(calls) == 1
    assert calls[0][0] == "AAPL"
    assert "now_ts" in calls[0][1]
    assert api._scan_status["quote_capability"]["interval_min"] == 15


def test_scheduler_control_probe_fails_when_snapshot_works_but_trades_are_forbidden(
    monkeypatch,
):
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")
    def _snapshot(*args, **kwargs):
        with api._STOCK_QUOTE_CAPABILITY_LOCK:
            api._STOCK_QUOTE_CAPABILITY["latest_probe"] = {
                "probe_id": 778,
                "endpoint_access": "accessible",
                "reason": None,
            }
        return {
            "ok": True,
            "age_seconds": 2.0,
            "observed_ts": time.time() - 2,
            "last_trade_ts": time.time() - 3,
            "_capability_probe_id": 778,
        }

    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", _snapshot)
    monkeypatch.setattr(
        api,
        "_stock_aggregate_capability_probe",
        lambda *args, **kwargs: {"ok": True, "status": "verified_bounded_1m_aggregates"},
    )
    monkeypatch.setattr(
        api,
        "_stock_trade_replay_capability_probe",
        lambda *args, **kwargs: {
            "ok": False,
            "reason": "trade_replay_probe_http_403",
        },
    )

    with pytest.raises(RuntimeError, match="trade.*http_403"):
        api._stock_quote_capability_control_probe()


def test_trade_replay_403_is_separate_runtime_capability_and_keeps_health_blocked(
    monkeypatch,
):
    now_ts = time.time()
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(403),
    )

    result = api._stock_trade_replay_capability_probe(
        "AAPL",
        {
            "observed_ts": now_ts - 1,
            "last_trade_ts": now_ts - 2,
        },
    )

    assert result == {"ok": False, "reason": "trade_replay_probe_http_403"}
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        api._STOCK_QUOTE_CAPABILITY.update({
            "last_verified_realtime_quote_at": api.datetime.now(
                api.timezone.utc
            ).isoformat(),
            "last_verified_quote_observed_at": now_ts - 1,
            "last_verified_quote_age_seconds": 1.0,
        })
        replay = dict(api._STOCK_QUOTE_CAPABILITY["trade_replay_endpoint"])
    assert replay["access"] == "forbidden"
    assert replay["http_status"] == 403

    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_: {
            "allowed": True,
            "session": "US_REGULAR",
            "reason": "unit regular",
        },
    )
    capability, public_value = api._stock_quote_capability_health_snapshot()
    assert public_value is True
    assert capability["realtime_quote"]["status"] == "verified"
    assert capability["bounded_trade_replay"]["status"] == "not_verified"
    assert capability["bounded_trade_replay"]["endpoint_access"] == "forbidden"
    assert capability["stock_trade_mail_evidence"]["status"] == "blocked"


@pytest.mark.parametrize("malformed_results", [{}, "", False, 0])
def test_trade_replay_probe_rejects_falsy_non_list_results(
    monkeypatch, malformed_results
):
    now_ts = time.time()
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "rate_limited_get",
        lambda *args, **kwargs: _Response(
            200,
            {"status": "OK", "results": malformed_results},
        ),
    )

    result = api._stock_trade_replay_capability_probe(
        "AAPL",
        {
            "observed_ts": now_ts - 1,
            "last_trade_ts": now_ts - 2,
        },
    )

    assert result == {"ok": False, "reason": "trade_replay_probe_incomplete"}


def test_health_rejects_fresh_quote_from_previous_session_and_hides_identity(
    monkeypatch,
):
    transition = api.datetime(2026, 8, 31, 13, 30, 20, tzinfo=api.timezone.utc)
    premarket_quote_ts = api.datetime(
        2026, 8, 31, 13, 29, 59, tzinfo=api.timezone.utc
    ).timestamp()
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_: {
            "allowed": True,
            "session": "US_REGULAR",
            "reason": "unit-test transition",
        },
    )
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        api._STOCK_QUOTE_CAPABILITY.update({
            "checked_at": "2026-08-31T13:30:01+00:00",
            "last_verified_realtime_quote_at": "2026-08-31T13:30:01+00:00",
            "last_verified_quote_observed_at": premarket_quote_ts,
            "last_verified_quote_age_seconds": 2.0,
            "last_verified_ticker": "PRIVATE",
            "latest_probe": {
                "probe_id": 99,
                "ticker": "PRIVATE",
                "endpoint_access": "accessible",
                "reason": None,
            },
        })

    capability, public_value = api._stock_quote_capability_health_snapshot(
        transition
    )

    assert public_value is False
    assert capability["status"] == "diagnostic_failed"
    assert capability["reason"] == "verified_quote_from_different_market_session"
    assert capability["realtime_quote"]["verified_market_session"] == "PREMARKET"
    assert capability["checked_at"] == "2026-08-31T13:30:01+00:00"
    assert capability["assessment_checked_at"] == transition.isoformat()
    assert "last_verified_ticker" not in capability
    assert "ticker" not in capability["latest_probe"]


def test_premarket_control_health_is_unprobed_diagnostic_not_delivery_block(
    monkeypatch,
):
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "PREMARKET")
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *_: {
            "allowed": True,
            "session": "PREMARKET",
            "reason": "unit premarket",
        },
    )

    capability, public_value = api._stock_quote_capability_health_snapshot()

    assert public_value is None
    assert capability["status"] == "control_not_run_for_premarket"
    assert capability["applicable"] is True
    assert capability["realtime_quote"]["status"] == "unverified"
    assert capability["bounded_trade_replay"]["status"] == "unverified"
    assert capability["bounded_minute_aggregates"]["status"] == "unverified"
    assert capability["stock_trade_mail_evidence"]["status"] == "unverified"
    assert "diagnostic" in capability["diagnostic_note"].lower()


def test_control_composite_generation_is_atomic_and_old_replay_cannot_overwrite(
    monkeypatch,
):
    old_replay_started = threading.Event()
    release_old_replay = threading.Event()
    call_lock = threading.Lock()
    replay_calls = 0

    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")

    def _get(url, **kwargs):
        nonlocal replay_calls
        if "/v2/snapshot/" in url:
            return _Response(
                200,
                _snapshot_payload(time.time(), include_last_trade=True),
            )
        if "/v2/aggs/" in url:
            return _Response(200, {"status": "OK", "results": []})
        if "/v3/trades/" in url:
            with call_lock:
                replay_calls += 1
                call_number = replay_calls
            if call_number == 1:
                old_replay_started.set()
                assert release_old_replay.wait(timeout=3)
                return _Response(403)
            return _Response(
                200,
                {
                    "status": "OK",
                    "results": [
                        {"sip_timestamp": kwargs["params"]["timestamp.lte"]}
                    ],
                },
            )
        raise AssertionError(url)

    monkeypatch.setattr(api, "rate_limited_get", _get)
    old_result = []
    old_thread = threading.Thread(
        target=lambda: old_result.append(
            api._stock_quote_capability_control_probe()
        )
    )
    old_thread.start()
    assert old_replay_started.wait(timeout=3)

    newer = api._stock_quote_capability_control_probe()
    release_old_replay.set()
    old_thread.join(timeout=3)

    assert newer["status"] == "verified_realtime_quote"
    assert old_result and old_result[0]["status"] == "superseded"
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        state = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert state["composite_probe"]["status"] == "verified"
    assert state["composite_probe"]["generation"] == newer["generation"]
    assert state["trade_replay_endpoint"]["http_status"] == 200
    assert (
        state["trade_replay_endpoint"]["composite_generation"]
        == newer["generation"]
    )

    capability, public_value = api._stock_quote_capability_health_snapshot()
    assert public_value is True
    assert capability["realtime_quote"]["status"] == "verified"
    assert capability["bounded_trade_replay"]["status"] == "verified"
    assert capability["stock_trade_mail_evidence"]["status"] == "verified"


def test_aggregate_403_blocks_combined_evidence_without_redacting_quote_or_replay(
    monkeypatch,
):
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")

    def _get(url, **kwargs):
        if "/v2/snapshot/" in url:
            return _Response(200, _snapshot_payload(time.time(), include_last_trade=True))
        if "/v2/aggs/" in url:
            return _Response(403)
        if "/v3/trades/" in url:
            return _Response(200, {
                "status": "OK",
                "results": [{"sip_timestamp": kwargs["params"]["timestamp.lte"]}],
            })
        raise AssertionError(url)

    monkeypatch.setattr(api, "rate_limited_get", _get)

    with pytest.raises(RuntimeError, match="aggregate_probe_http_403"):
        api._stock_quote_capability_control_probe()

    capability, public_value = api._stock_quote_capability_health_snapshot()
    assert public_value is True
    assert capability["realtime_quote"]["status"] == "verified"
    assert capability["bounded_trade_replay"]["status"] == "verified"
    assert capability["bounded_minute_aggregates"]["status"] == "not_verified"
    assert capability["bounded_minute_aggregates"]["endpoint_access"] == "forbidden"
    assert capability["stock_trade_mail_evidence"]["status"] == "blocked"


def test_candidate_snapshot_cannot_supersede_slow_control_composite(monkeypatch):
    replay_started = threading.Event()
    release_replay = threading.Event()
    monkeypatch.setattr(api, "POLYGON_KEY", "unit-key")
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda *_: "US_REGULAR")

    def _get(url, **kwargs):
        if "/v2/snapshot/" in url:
            return _Response(200, _snapshot_payload(time.time(), include_last_trade=True))
        if "/v2/aggs/" in url:
            return _Response(200, {"status": "OK", "results": []})
        if "/v3/trades/" in url:
            replay_started.set()
            assert release_replay.wait(timeout=3)
            return _Response(200, {
                "status": "OK",
                "results": [{"sip_timestamp": kwargs["params"]["timestamp.lte"]}],
            })
        raise AssertionError(url)

    monkeypatch.setattr(api, "rate_limited_get", _get)
    control_result = []
    thread = threading.Thread(
        target=lambda: control_result.append(api._stock_quote_capability_control_probe())
    )
    thread.start()
    assert replay_started.wait(timeout=3)

    candidate = api._fetch_stock_revalidation_snapshot("MSFT")
    assert candidate["ok"] is True
    release_replay.set()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert control_result and control_result[0]["status"] == "verified_realtime_quote"
    generation = control_result[0]["generation"]
    with api._STOCK_QUOTE_CAPABILITY_LOCK:
        state = copy.deepcopy(api._STOCK_QUOTE_CAPABILITY)
    assert state["composite_probe"]["generation"] == generation
    assert state["composite_probe"]["status"] == "verified"
    assert state["control_quote"]["generation"] == generation

    capability, public_value = api._stock_quote_capability_health_snapshot()
    assert public_value is True
    assert capability["realtime_quote"]["status"] == "verified"
    assert capability["bounded_trade_replay"]["status"] == "verified"
    assert capability["bounded_minute_aggregates"]["status"] == "verified"
    assert capability["stock_trade_mail_evidence"]["status"] == "verified"
