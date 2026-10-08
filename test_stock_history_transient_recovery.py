"""Strict stock history retries transient GET failures, never evidence failures.

These tests exercise the real fetcher and daily parser. Only HTTP and the
external sleeping boundary are controlled; prices/dates, parsing and failures
are not mocked. A recovered request cannot invent or partially repair a bar.
"""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from modules import data_fetchers as fetchers
from modules import stock_scan_runtime


NOW = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


def _payload(**changes):
    record = {"status": "OK", "adjusted": True, "queryCount": 1,
              "resultsCount": 1, "results": [{"t": 1790222400000,
                  "o": 10., "h": 11., "l": 9., "c": 10.5, "v": 10_000.}]}
    record.update(changes)
    return record


EXPECTED = [{"date": "2026-09-24", "open": 10., "high": 11., "low": 9.,
             "close": 10.5, "volume": 10_000.}]


def _provider(monkeypatch, answers):
    """Every attempt observes the same query; credentials are offline literals."""
    calls = []

    def request(url, **kwargs):
        calls.append((url, deepcopy(kwargs)))
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        status, body = answer

        def json():
            if isinstance(body, Exception):
                raise body
            return deepcopy(body)

        return SimpleNamespace(status_code=status, json=json)

    monkeypatch.setattr(fetchers, "rate_limited_get", request)
    waits = []
    # The real budgeted_wait still executes both runtime checkpoints. Only the
    # actual sleeping dependency is external to this deterministic fixture.
    monkeypatch.setattr(fetchers.time, "sleep", waits.append)
    return calls, waits


def _fetch(*, ticker="TEST", diagnostics=None, budget=None):
    return fetchers.fetch_stock_daily_history_strict(ticker, "offline-test-only",
        as_of=NOW, completed_through="2026-09-24", diagnostics=diagnostics,
        retry_state=budget)


@pytest.mark.parametrize("failure", [
    fetchers.requests.exceptions.Timeout("offline transport timeout"),
    fetchers.requests.exceptions.ConnectionError("offline interrupted connection"),
    (500, {}), (502, {}), (503, {}), (504, {}),
], ids=["timeout", "connection", "http500", "http502", "http503", "http504"])
def test_identical_transient_get_retry_recovers_unchanged_valid_daily_history(monkeypatch, failure):
    calls, waits = _provider(monkeypatch, [failure, (200, _payload())])
    budget = {"used": 0}
    assert _fetch(budget=budget) == EXPECTED
    assert len(calls) == 2 and calls[0] == calls[1]
    assert waits == [0.5]
    assert budget["used"] == 1


def test_two_transient_failures_recover_on_third_identical_query(monkeypatch):
    failure = fetchers.requests.exceptions.Timeout("offline timeout")
    calls, waits = _provider(monkeypatch, [failure, (503, {}), (200, _payload())])
    budget = {"used": 0}
    assert _fetch(budget=budget) == EXPECTED
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert waits == [0.5, 1.0]
    assert budget["used"] == 2


@pytest.mark.parametrize("failure,reason", [
    (fetchers.requests.exceptions.Timeout("offline"), "timeout"),
    (fetchers.requests.exceptions.ConnectionError("offline"), "connection_failure"),
    ((503, {}), "http_server_error"),
])
def test_permanent_transient_failure_stops_after_three_requests_not_symbol_exclusion(monkeypatch, failure, reason):
    calls, waits = _provider(monkeypatch, [failure])
    budget = {"used": 0}
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(budget=budget)
    assert error.value.code == "scan_data_unavailable"
    assert error.value.reason == reason
    assert error.value.symbol_local is False
    assert len(calls) == 3 and calls[0] == calls[1] == calls[2]
    assert waits == [0.5, 1.0]
    assert budget["used"] == 2


@pytest.mark.parametrize("failure,reason,code", [
    ((401, {}), "http_unauthorized", "scan_provider_unauthorized"),
    ((403, {}), "http_unauthorized", "scan_provider_unauthorized"),
    ((429, {}), "http_rate_limited", "scan_provider_rate_limited"),
    ((501, {}), "http_server_error", "scan_data_unavailable"),
    ((404, {}), "http_client_error", "scan_data_unavailable"),
    (fetchers.requests.exceptions.SSLError("offline certificate failure"),
     "tls_failure", "scan_data_unavailable"),
    ((200, ValueError("offline malformed JSON")), "invalid_json", "scan_data_invalid"),
    ((200, {"status": "OK", "resultsCount": 1}), "missing_results", "scan_data_invalid"),
    ((200, _payload(resultsCount=2)), "result_count_mismatch", "scan_data_invalid"),
    ((200, _payload(next_url="https://example.invalid/never-follow")),
     "unexpected_pagination", "scan_data_invalid"),
])
def test_transport_recovery_never_retries_authority_tls_json_or_schema_failures(monkeypatch, failure, reason, code):
    calls, waits = _provider(monkeypatch, [failure, (200, _payload())])
    budget = {"used": 0}
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(budget=budget)
    assert (error.value.code, error.value.reason, error.value.symbol_local) == (code, reason, False)
    assert len(calls) == 1 and waits == [] and budget["used"] == 0


def test_transport_retry_cannot_repair_malformed_recovered_observation(monkeypatch):
    bad = _payload()
    bad["results"][0]["t"] = 0
    calls, waits = _provider(monkeypatch, [(503, {}), (200, bad), (200, _payload())])
    budget = {"used": 0}
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(budget=budget)
    assert error.value.code == "scan_data_invalid" and error.value.field == "t"
    assert error.value.symbol_local is False
    assert len(calls) == 2 and waits == [0.5] and budget["used"] == 1


def test_twenty_retry_budget_is_shared_by_different_symbols(monkeypatch):
    failure = fetchers.requests.exceptions.Timeout("offline")
    calls, waits = _provider(monkeypatch, [failure, (200, _payload()), failure])
    budget = {"used": 19}
    assert _fetch(ticker="FIRST", budget=budget) == EXPECTED
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(ticker="SECOND", budget=budget)
    assert error.value.reason == "timeout" and not error.value.symbol_local
    assert len(calls) == 3 and calls[0] == calls[1]
    assert waits == [0.5] and budget["used"] == 20


def test_ohlcv_retry_cannot_spend_a_budget_consumed_by_transport_recovery(monkeypatch):
    bad = _payload()
    bad["results"][0]["o"] = 0
    calls, waits = _provider(monkeypatch, [(503, {}), (200, bad), (200, _payload())])
    budget = {"used": 19}
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(budget=budget)
    assert error.value.reason == "invalid_bar_value" and error.value.symbol_local
    assert len(calls) == 2 and waits == [0.5] and budget["used"] == 20


def test_transport_retry_cannot_spend_a_budget_consumed_by_ohlcv_recovery(monkeypatch):
    bad = _payload()
    bad["results"][0]["o"] = 0
    failure = fetchers.requests.exceptions.Timeout("offline")
    calls, waits = _provider(monkeypatch, [(200, bad), (200, _payload()), failure])
    budget = {"used": 19}
    assert _fetch(ticker="FIRST", budget=budget) == EXPECTED
    with pytest.raises(fetchers.StockHistoryDataError) as error:
        _fetch(ticker="SECOND", budget=budget)
    assert error.value.reason == "timeout" and not error.value.symbol_local
    assert len(calls) == 3 and calls[0] == calls[1]
    assert waits == [] and budget["used"] == 20


def test_retry_backoff_respects_owning_scan_deadline(monkeypatch):
    failure = fetchers.requests.exceptions.Timeout("offline")
    calls, waits = _provider(monkeypatch, [failure, (200, _payload())])
    ticks = {"now": 0.}
    monkeypatch.setattr(stock_scan_runtime.time, "monotonic", lambda: ticks["now"])

    def sleep(seconds):
        waits.append(seconds)
        ticks["now"] += seconds

    monkeypatch.setattr(fetchers.time, "sleep", sleep)
    with stock_scan_runtime.scope("turtle_or_cup") as runtime:
        runtime["deadline"] = 0.25
        with pytest.raises(stock_scan_runtime.ScanWorkTimeout):
            _fetch(budget={"used": 0})
    assert len(calls) == 1
    assert waits == [0.25]
