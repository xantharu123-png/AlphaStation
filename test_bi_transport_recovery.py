"""Real BI lifecycle with deterministic, offline transport failures."""
import json
import traceback

import pytest
from requests.exceptions import ConnectionError, SSLError, Timeout

import modules.scanners as scanners
from test_bi_deep_fixes_scan import _flat_bars, _to_polygon
from test_bi_diagnostics_integration import _io, _result


class Reply:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def valid():
    bars = _to_polygon(_flat_bars())
    return Reply(payload={"status": "OK", "results": bars,
                          "resultsCount": len(bars), "queryCount": len(bars)})


def setup(monkeypatch, tmp_path, replies, *, count=1, direction="long"):
    tickers, snapshots = _io(monkeypatch, tmp_path, _result(16), count=count)
    final = tmp_path / (direction + ".json")
    final.write_text("previous-final", encoding="utf8")
    pending = iter(replies)
    requests, sleeps = [], []

    def get(url, params=None, timeout=15):
        requests.append((url, params, timeout))
        reply = next(pending)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(scanners, "rate_limited_get", get)
    monkeypatch.setattr(scanners.time, "sleep", sleeps.append)
    return tickers, final, requests, sleeps, snapshots


def progress(tmp_path, direction="long"):
    return json.loads((tmp_path / (direction + "-progress.json")).read_text())


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("failure,reason", [
    (Timeout("PRIVATE URL apiKey=secret"), "timeout"),
    (ConnectionError("PRIVATE connection"), "connection_failure"),
    (Reply(408), "http_request_timeout"), (Reply(500), "http_server_error"),
    (Reply(502), "http_server_error"), (Reply(503), "http_server_error"),
    (Reply(504), "http_server_error"),
])
def test_transient_history_failure_recovers_to_strict_zero(monkeypatch, tmp_path, direction, failure, reason):
    tickers, final, calls, sleeps, _ = setup(monkeypatch, tmp_path, [failure, valid()], direction=direction)
    scanners._bi_background_scan("fixture", direction, tickers)
    cache = json.loads(final.read_text())
    d = cache["diagnostics"]
    assert cache["results"] == [] and d["final_results"] == 0
    assert d["coverage"] == "complete" and d["data_failures"] == 0
    assert d["confluence"]["green_count_histogram"]["16"] == 1
    assert d["rejected"]["indicator_or_hard_gate_contract"] == 1
    assert d["transport_recovered_incidents"] == 1
    assert d["transport_error_counts"] == {reason: 1}
    assert d["transport_requests"] == 2 and d["transport_retries"] == 1
    assert "transport_error_reason" not in d
    assert calls[0] == calls[1] and sleeps == [0.5]
    assert progress(tmp_path, direction)["status"] == "done"


@pytest.mark.parametrize("failure,reason,code,attempts", [
    (Timeout("PRIVATE timeout"), "timeout", "scan_data_unavailable", 3),
    (Reply(401), "http_unauthorized", "scan_provider_unauthorized", 1),
    (Reply(403), "http_unauthorized", "scan_provider_unauthorized", 1),
    (Reply(429), "http_rate_limited", "scan_provider_rate_limited", 1),
    (Reply(404), "http_client_error", "scan_data_unavailable", 1),
    (Reply(501), "http_server_error", "scan_data_unavailable", 1),
    (Reply(302), "http_unexpected_status", "scan_data_unavailable", 1),
    (SSLError("PRIVATE TLS"), "tls_failure", "scan_data_unavailable", 1),
    (Reply(payload=ValueError("PRIVATE JSON")), "malformed_json", "scan_data_invalid", 1),
    (RuntimeError("PRIVATE unexpected"), "unexpected_failure", "scan_data_unavailable", 1),
])
def test_terminal_failure_is_safe_and_preserves_final(monkeypatch, tmp_path, capsys, failure, reason, code, attempts):
    tickers, final, calls, sleeps, _ = setup(monkeypatch, tmp_path, [failure] * attempts)
    with pytest.raises(scanners.ScannerDataError, match=code) as caught:
        scanners._bi_background_scan("fixture", "long", tickers)
    p = progress(tmp_path)
    d = p["diagnostics"]
    assert final.read_text() == "previous-final"
    assert p["status"] == "error" and d["coverage"] == "incomplete" and d["final_results"] is None
    assert d["data_failures"] == 1 and d["analyzed"] == 0
    assert d["transport_error_reason"] == reason
    assert d["transport_error_counts"] == {reason: attempts}
    assert d["transport_recovered_incidents"] == 0
    assert d["transport_requests"] == len(calls) == attempts
    assert sleeps == ([0.5, 1.0] if attempts == 3 else [])
    assert "PRIVATE" not in json.dumps(p) + "".join(traceback.format_exception(caught.value)) + capsys.readouterr().out


def test_stop_during_retry_backoff_preserves_final(monkeypatch, tmp_path):
    tickers, final, calls, sleeps, _ = setup(monkeypatch, tmp_path, [Timeout("PRIVATE"), valid()])
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: bool(sleeps))
    scanners._bi_background_scan("fixture", "long", tickers)
    p = progress(tmp_path)
    assert len(calls) == 1 and sleeps == [0.5]
    assert final.read_text() == "previous-final"
    assert p["status"] == "stopped" and p["diagnostics"]["coverage"] == "incomplete"
    assert p["diagnostics"]["final_results"] is None


def test_shared_retry_budget_stops_twenty_first_extra_request(monkeypatch, tmp_path):
    replies = [item for _ in range(20) for item in (Timeout("PRIVATE"), valid())]
    replies += [Timeout("PRIVATE"), valid()]
    tickers, final, calls, sleeps, _ = setup(monkeypatch, tmp_path, replies, count=21)
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable"):
        scanners._bi_background_scan("fixture", "long", tickers)
    d = progress(tmp_path)["diagnostics"]
    assert final.read_text() == "previous-final"
    assert len(calls) == 41 and len(sleeps) == 20
    assert d["transport_retries"] == 20 and d["transport_retry_budget_exhausted"] == 1
    assert d["transport_recovered_incidents"] == d["analyzed"] == 20
    assert d["data_failures"] == 1 and d["coverage"] == "incomplete"


def test_required_universe_recovers_but_optional_movers_do_not_retry(monkeypatch, tmp_path):
    _, final, calls, sleeps, _ = setup(monkeypatch, tmp_path, [
        Timeout("PRIVATE"), Reply(payload={"status": "OK", "results": [{"ticker": "TEST"}]}),
        Timeout("PRIVATE optional"), Reply(503), valid(),
    ])
    scanners._bi_background_scan("fixture", "long")
    d = json.loads(final.read_text())["diagnostics"]
    assert d["coverage"] == "complete" and d["data_failures"] == 0
    assert len(calls) == 5 and sleeps == [0.5]
    assert d["transport_requests"] == 3 and d["transport_recovered_incidents"] == 1


@pytest.mark.parametrize("preloaded", [True, False])
def test_stop_before_first_required_request_preserves_final(monkeypatch, tmp_path, preloaded):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [valid()])
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: True)
    scanners._bi_background_scan("fixture", "long", tickers if preloaded else None)
    assert not calls and final.read_text() == "previous-final"
    assert progress(tmp_path)["status"] == "stopped"


def test_stop_between_candidates_keeps_old_final(monkeypatch, tmp_path):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [valid(), valid()], count=2)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: bool(calls))
    scanners._bi_background_scan("fixture", "long", tickers)
    assert len(calls) == 1 and final.read_text() == "previous-final"
    assert progress(tmp_path)["status"] == "stopped"


def test_stop_after_recovered_final_request_never_publishes_success(monkeypatch, tmp_path):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [Timeout("PRIVATE"), valid()])
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: len(calls) == 2)
    scanners._bi_background_scan("fixture", "long", tickers)
    assert final.read_text() == "previous-final"
    assert progress(tmp_path)["status"] == "stopped"


def test_stop_during_universe_is_not_cleared_before_history(monkeypatch, tmp_path):
    _, final, calls, _, _ = setup(monkeypatch, tmp_path, [
        Reply(payload={"status": "OK", "results": [{"ticker": "TEST"}]}),
        Reply(payload={"tickers": []}), Reply(payload={"tickers": []}), valid(),
    ])
    state = {"cleared_after_request": False}

    def clear(direction):
        if calls:
            state["cleared_after_request"] = True

    monkeypatch.setattr(scanners, "_bi_clear_stop", clear)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: bool(calls) and not state["cleared_after_request"])
    scanners._bi_background_scan("fixture", "long")
    assert len(calls) == 1 and final.read_text() == "previous-final"
    assert progress(tmp_path)["status"] == "stopped"


def test_old_stop_is_cleared_once_before_universe_requests(monkeypatch, tmp_path):
    _, final, calls, _, _ = setup(monkeypatch, tmp_path, [
        Reply(payload={"status": "OK", "results": [{"ticker": "TEST"}]}),
        Reply(payload={"tickers": []}), Reply(payload={"tickers": []}), valid(),
    ])
    state = {"stop": True}
    monkeypatch.setattr(scanners, "_bi_clear_stop", lambda direction: state.update(stop=False))
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: state["stop"])
    scanners._bi_background_scan("fixture", "long")
    assert len(calls) == 4
    assert json.loads(final.read_text())["diagnostics"]["coverage"] == "complete"
