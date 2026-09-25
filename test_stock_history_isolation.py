"""Typed stock history and whole-symbol isolation, exclusively offline fixtures."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import api
from modules import data_fetchers as fetchers
from modules.bi_market_data import BIAggregateDataError
from test_cup_runtime_admission import _cup_fixture, NAME
from test_stock_strategy_attempt_status import status_io, attempt_payload, write_attempt

NOW = datetime(2026, 9, 25, 14, 0, tzinfo=timezone.utc)


def bar(**changes):
    value = {"t": 1790222400000, "o": 10., "h": 11., "l": 9., "c": 10.5, "v": 10000.}
    value.update(changes)
    return value


def payload(rows=None, **changes):
    rows = [bar()] if rows is None else rows
    return {"status": "OK", "resultsCount": len(rows), "results": rows, **changes}


def response(monkeypatch, answers):
    calls = []
    def request(url, **kwargs):
        calls.append((url, deepcopy(kwargs)))
        answer = answers[min(len(calls) - 1, len(answers) - 1)]
        if isinstance(answer, Exception):
            raise answer
        status, data = answer
        def json():
            if isinstance(data, Exception):
                raise data
            return deepcopy(data)
        return SimpleNamespace(status_code=status, json=json)
    monkeypatch.setattr(fetchers, "rate_limited_get", request)
    return calls


def fetch(**kwargs):
    return fetchers.fetch_stock_daily_history_strict("TEST", "offline", as_of=NOW,
        completed_through="2026-09-24", **kwargs)


@pytest.mark.parametrize("empty", [payload([]), {"status": "OK", "resultsCount": 0}])
def test_explicit_empty_success_is_not_global_failure(monkeypatch, empty):
    calls = response(monkeypatch, [(200, empty)])
    assert fetch() == []
    assert len(calls) == 1


@pytest.mark.parametrize("status,body,code,reason", [
    (401, {}, "scan_provider_unauthorized", "http_unauthorized"),
    (403, {}, "scan_provider_unauthorized", "http_unauthorized"),
    (429, {}, "scan_provider_rate_limited", "http_rate_limited"),
    (500, {}, "scan_data_unavailable", "http_server_error"),
    (404, {}, "scan_data_unavailable", "http_client_error"),
    (302, {}, "scan_data_unavailable", "http_unexpected_status"),
    (200, payload(status="NOT_AUTHORIZED"), "scan_provider_unauthorized", "http_unauthorized"),
    (200, payload(status="RATE_LIMITED"), "scan_provider_rate_limited", "http_rate_limited"),
    (200, {"private": "secret"}, "scan_data_invalid", "missing_results"),
    (200, payload(next_url="https://secret"), "scan_data_invalid", "unexpected_pagination"),
    (200, payload(resultsCount=2), "scan_data_invalid", "result_count_mismatch"),
    (200, ValueError("secret key in JSON error"), "scan_data_invalid", "invalid_json"),
])
def test_systemic_errors_keep_reason_and_never_become_local(monkeypatch, status, body, code, reason):
    calls = response(monkeypatch, [(status, body)])
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch()
    assert (caught.value.code, caught.value.reason, caught.value.symbol_local) == (code, reason, False)
    assert "secret" not in str(caught.value)
    assert len(calls) == 1


@pytest.mark.parametrize("exception,reason", [
    (fetchers.requests.exceptions.Timeout("secret"), "timeout"),
    (fetchers.requests.exceptions.ConnectionError("secret"), "connection_failure"),
    (fetchers.requests.exceptions.SSLError("secret"), "tls_failure"),
])
def test_transport_not_isolated_or_retried_as_symbol(monkeypatch, exception, reason):
    calls = response(monkeypatch, [exception])
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch()
    assert caught.value.reason == reason
    assert not caught.value.symbol_local
    assert len(calls) == 1


@pytest.mark.parametrize("changes", [
    {"o": 0}, {"o": "not-a-number"}, {"o": None}, {"c": float("nan")},
    {"h": 8}, {"v": -1}, {"c": True},
])
def test_invalid_symbol_never_consumes_partially_repaired_history(monkeypatch, changes):
    broken = payload([bar(**changes)])
    calls = response(monkeypatch, [(200, broken)])
    diagnostics = {}
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch(diagnostics=diagnostics)
    assert caught.value.symbol_local
    assert len(calls) == 2 and calls[0] == calls[1]
    assert diagnostics == {"data_retry_attempts": 1, "data_retry_failed": 1}


def test_missing_ohlc_is_local_not_provider_unavailable(monkeypatch):
    broken = bar()
    del broken["o"]
    response(monkeypatch, [(200, payload([broken]))])
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch()
    assert (caught.value.code, caught.value.field, caught.value.value_class) == ("scan_data_invalid", "o", "missing")
    assert caught.value.symbol_local


@pytest.mark.parametrize("rows", [[bar(t=0)], [bar(), bar()], [bar(t=NOW.timestamp()*1000 + 1000)]])
def test_timestamp_errors_remain_systemic(monkeypatch, rows):
    calls = response(monkeypatch, [(200, payload(rows))])
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch()
    assert not caught.value.symbol_local
    assert len(calls) == 1


def test_retry_recovery_requires_same_observation_timestamps(monkeypatch):
    calls = response(monkeypatch, [(200, payload([bar(o=0)])), (200, payload())])
    diagnostics = {}
    result = fetch(diagnostics=diagnostics)
    assert result == [{"date": "2026-09-24", "open": 10., "high": 11., "low": 9., "close": 10.5, "volume": 10000.}]
    assert diagnostics == {"data_retry_attempts": 1, "data_retry_recovered": 1}
    assert calls[0] == calls[1]


@pytest.mark.parametrize("retry", [payload([]), payload([bar(t=1790136000000)])])
def test_retry_cannot_hide_bad_observation_by_shortening_query(monkeypatch, retry):
    response(monkeypatch, [(200, payload([bar(o=0)])), (200, retry)])
    diagnostics = {}
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch(diagnostics=diagnostics)
    assert caught.value.symbol_local
    assert diagnostics["data_retry_observation_mismatches"] == 1
    assert diagnostics["data_retry_failed"] == 1


def test_retry_systemic_failure_never_downgrades_to_symbol_exclusion(monkeypatch):
    response(monkeypatch, [(200, payload([bar(o=0)])), (429, {})])
    with pytest.raises(fetchers.StockHistoryDataError) as caught:
        fetch()
    assert caught.value.code == "scan_provider_rate_limited" and not caught.value.symbol_local


def test_retry_budget_is_shared_and_finite(monkeypatch):
    calls = response(monkeypatch, [(200, payload([bar(o=0)]))])
    budget, diagnostics = {"used": 20}, {}
    with pytest.raises(fetchers.StockHistoryDataError):
        fetch(diagnostics=diagnostics, retry_state=budget)
    assert len(calls) == 1 and budget["used"] == 20
    assert diagnostics == {"data_retry_budget_exhausted": 1}


def test_uncompleted_session_prices_do_not_contaminate_completed_history(monkeypatch):
    future_session = bar(t=1790308800000, o=0)
    response(monkeypatch, [(200, payload([bar(), future_session]))])
    assert len(fetch()) == 1


def local_error():
    return fetchers.StockHistoryDataError("scan_data_invalid", "invalid_bar_value",
        bar_error=BIAggregateDataError("invalid_bar_value", field="o", value_class="zero_price", position="first"))


@pytest.mark.parametrize("stage", [70, 180])
def test_cup_whole_symbol_exclusion_keeps_valid_siblings_and_explicit_coverage(monkeypatch, stage):
    original = api._fetch_strategy_daily_history
    state = _cup_fixture(monkeypatch, count=3)
    if stage == 70:
        monkeypatch.setattr(api, "_fetch_strategy_daily_history", original)
        def history(symbol, *args, **kwargs):
            if symbol == "T001":
                raise local_error()
            return state["bars"]
        monkeypatch.setattr(api, "fetch_stock_daily_history_strict", history)
    else:
        inherited = api._fetch_strategy_daily_history
        def history(symbol, minimum, *args):
            if symbol == "T001" and minimum == 180:
                raise local_error()
            return inherited(symbol, minimum, *args)
        monkeypatch.setattr(api, "_fetch_strategy_daily_history", history)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert [row["ticker"] for row in rows] == ["T000", "T002"]
    diag = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diag["coverage"] == "complete_with_exclusions"
    assert diag["excluded_data_symbols"] == diag["invalid_history_symbols"] == 1
    assert diag["rejected"]["invalid_daily_history"] == 1
    assert state["attempts"][-1][0] == "complete"
    assert api._stock_strategy_attempt_diagnostics(diag, sweep=False)["coverage"] == "complete_with_exclusions"
    if stage == 180:
        assert diag["special_filter_checked_count"] == 3
        assert diag["special_filter_unexamined_count"] == 0
        assert diag["cup_terminal_counts"]["invalid_daily_history"] == 1


def test_valid_empty_stock_history_is_counted_without_aborting_siblings(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    inherited = api._fetch_strategy_daily_history
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda symbol, *args:
                        [] if symbol == "T001" else inherited(symbol, *args))
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert [row["ticker"] for row in rows] == ["T000", "T002"]
    diag = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diag["empty_history_symbols"] == 1
    assert diag["rejected"]["empty_daily_history"] == 1
    assert diag["coverage"] == "complete"


def test_many_invalid_symbols_fail_leaf_instead_of_hiding_provider_incident(monkeypatch):
    state = _cup_fixture(monkeypatch, count=30)
    def invalid(*args):
        raise local_error()
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", invalid)
    with pytest.raises(api.ScannerDataError, match="scan_data_invalid"):
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert not state["writes"]
    assert state["attempts"][-1][0] == "error"
    diag = state["attempts"][-1][1]["diagnostics"]
    assert diag["excluded_data_symbols"] == 20
    assert diag["stock_history_error_counts"]["symbol_exclusion_limit"] == 1
    assert diag["coverage"] == "incomplete"


@pytest.mark.parametrize("code", ["scan_provider_unauthorized", "scan_provider_rate_limited", "scan_data_unavailable"])
def test_systemic_preselection_error_preserves_global_failure(monkeypatch, code):
    original = api._fetch_strategy_daily_history
    state = _cup_fixture(monkeypatch, count=3)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", original)
    def fail(*a, **kw):
        raise fetchers.StockHistoryDataError(code, "http_rate_limited" if code.endswith("limited") else "http_unauthorized")
    monkeypatch.setattr(api, "fetch_stock_daily_history_strict", fail)
    with pytest.raises(api.ScannerDataError, match=code):
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert not state["writes"]
    assert state["attempts"][-1][0] == "error"


def test_complete_with_exclusions_attempt_is_valid_persisted_evidence(status_io):
    root, _ = status_io
    value = attempt_payload(NAME, status="complete")
    value["diagnostics"].update(coverage="complete_with_exclusions", excluded_data_symbols=1,
                                invalid_history_symbols=1,
                                stock_history_error_counts={"invalid_bar_value": 1, "private-symbol": 2})
    write_attempt(root, value)
    evidence = api._read_stock_strategy_attempt(NAME)
    assert evidence["available"] and evidence["status"] == "complete"
    assert evidence["diagnostics"]["coverage"] == "complete_with_exclusions"
    assert evidence["diagnostics"]["stock_history_error_counts"] == {"invalid_bar_value": 1}


def test_newer_complete_exclusions_cache_is_recovery_not_stale_failure(status_io):
    root, cache = status_io
    write_attempt(root, attempt_payload(NAME))
    cache.update(stamp="2026-09-14T12:01:00+00:00", coverage="complete_with_exclusions")
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.scan_error is None
    assert result.diagnostics["coverage"] == "complete_with_exclusions"
    assert "attempt_diagnostics" not in result.diagnostics


def test_runtime_cancellation_is_not_flattened_into_provider_failure(monkeypatch):
    response(monkeypatch, [api.stock_scan_runtime.ScanWorkTimeout()])
    with pytest.raises(api.stock_scan_runtime.ScanWorkTimeout):
        fetch()


def test_provider_window_and_request_contract_remain_adjusted_daily(monkeypatch):
    calls = response(monkeypatch, [(200, payload())])
    fetch()
    url, args = calls[0]
    assert url.endswith("/range/1/day/2023-09-26/2026-09-25")
    assert args == {"params": {"apiKey": "offline", "adjusted": "true", "sort": "asc", "limit": 50000}, "timeout": 15}
