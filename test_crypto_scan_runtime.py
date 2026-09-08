"""No real HTTP: validate opt-in per-host pacing and provider backoff."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import format_datetime
import threading

import pytest

from modules import crypto_scan_runtime as rt


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = {} if payload is None else payload
        self.headers = headers or {}

    def json(self):
        return self.payload


@pytest.fixture(autouse=True)
def isolated_budget(monkeypatch):
    monkeypatch.setattr(rt, "_STATES", {})
    monkeypatch.setattr(rt, "_LOCAL", threading.local())


def test_legacy_call_is_unchanged_without_opt_in():
    response = Response(429)
    assert rt.scan_http_get(lambda *_a, **_k: response, "https://api.bybit.com/x") is response
    assert not rt._STATES


def test_request_pacing_is_per_host_and_scope_is_restored(monkeypatch):
    clock, starts = [10.0], []
    monkeypatch.setattr(rt.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(rt.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    def get(url, **kwargs):
        starts.append((url, clock[0]))
        assert kwargs["timeout"] == 12
        return Response()
    with rt.paced_scan_requests():
        rt.scan_http_get(get, "https://api.bybit.com/a", timeout=12)
        with rt.paced_scan_requests():
            rt.scan_http_get(get, "https://api.bybit.com/b", timeout=12)
        rt.scan_http_get(get, "https://api.bitget.com/a", timeout=12)
    assert [start for _, start in starts] == [10.0, 10.25, 10.25]
    assert rt._LOCAL.paced is False


@pytest.mark.parametrize("response,floor", [
    (Response(429), 60), (Response(403), 600), (Response(418), 600),
    (Response(payload={"retCode": 10006}), 60),
    (Response(payload={"code": -1003}), 60),
    (Response(429, headers={"Retry-After": "3600"}), 3600),
])
def test_rate_limit_stops_followup_http_and_preserves_cooldown(monkeypatch, response, floor):
    clock, calls = [100.0], []
    monkeypatch.setattr(rt.time, "monotonic", lambda: clock[0])
    def get(*_a, **_k):
        calls.append(True)
        return response
    with rt.paced_scan_requests():
        for _ in range(2):
            with pytest.raises(rt.ScanRequestError, match="rate_limit"):
                rt.scan_http_get(get, "https://api.bybit.com/x?secret=NEVER_LOG")
    assert calls == [True]
    assert rt._STATES["api.bybit.com"]["blocked"] == 100 + floor
    clock[0] += floor + 1
    with rt.paced_scan_requests():
        assert rt.scan_http_get(lambda *_a, **_k: Response(), "https://api.bybit.com/x").status_code == 200


def test_retry_after_date_and_bybit_reset_timestamp(monkeypatch):
    monkeypatch.setattr(rt.time, "time", lambda: 1000.0)
    date = format_datetime(datetime.fromtimestamp(1900, timezone.utc), usegmt=True)
    assert rt._retry_delay({"Retry-After": date}, 60) == 900
    assert rt._retry_delay({"X-Bapi-Limit-Reset-Timestamp": "1500000"}, 60) == 500
    for raw in ("nan", "inf", "-10", "broken"):
        assert rt._retry_delay({"Retry-After": raw}, 60) == 60


def test_transport_error_is_safe_and_not_fabricated_empty_data():
    def fail(*_a, **_k):
        raise TimeoutError("apiKey=PRIVATE")
    with rt.paced_scan_requests(), pytest.raises(rt.ScanRequestError) as err:
        rt.scan_http_get(fail, "https://api.bybit.com/x?apiKey=PRIVATE")
    assert err.value.reason == "transport_error"
    assert "PRIVATE" not in str(err.value)


def test_different_hosts_can_request_concurrently():
    barrier = threading.Barrier(4)
    def request(host):
        with rt.paced_scan_requests():
            return rt.scan_http_get(lambda *_a, **_k: (barrier.wait(timeout=3), Response())[1], f"https://{host}/x")
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(request, ["api.bybit.com", "fapi.binance.com", "api.bitget.com", "contract.mexc.com"]))
    assert len(responses) == 4
