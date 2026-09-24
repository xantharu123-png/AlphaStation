"""Offline aggregate-response contract and real BI lifecycle regressions.

No real HTTP, app/API import, mail, broker, or production-cache access. Parser
fixtures are synthetic; lifecycle I/O uses the established isolated BI fixture.
"""
import copy
import json
import traceback

import pytest

from modules.bi_market_data import BIAggregateDataError, parse_bi_daily_aggregates


def _bars():
    return [
        {"t": 1788000000000, "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"t": 1788086400000, "o": 11, "h": 13, "l": 10, "c": 12, "v": 0},
    ]


def _unconvertible_history():
    bars = [dict(_bars()[0], t=1788000000000 + i * 86400000) for i in range(10)]
    bars[-1]["t"] = 1e308
    return {"results": bars}


def _error(payload, reason):
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates(payload)
    assert caught.value.reason == reason
    assert "PRIVATE" not in str(caught.value)


@pytest.mark.parametrize("reason", ["PRIVATE", None, 17, {"PRIVATE": True}])
def test_unknown_error_reason_is_sanitized_to_fixed_fallback(reason):
    error = BIAggregateDataError(reason)
    assert error.reason == str(error) == "invalid_payload"


@pytest.mark.parametrize("status", ["OK", "DELAYED"])
@pytest.mark.parametrize("query", [None, 0])
@pytest.mark.parametrize("pagination", [None, ""])
def test_documented_empty_response_can_omit_results(status, query, pagination):
    payload = {"status": status, "resultsCount": 0, "next_url": pagination}
    if query is not None:
        payload["queryCount"] = query
    before = copy.deepcopy(payload)
    assert parse_bi_daily_aggregates(payload) == []
    assert payload == before


@pytest.mark.parametrize("extra", [{}, {"status": "OK"}, {"status": "DELAYED"},
                                    {"resultsCount": 0}, {"queryCount": 0},
                                    {"resultsCount": 0, "queryCount": 0}])
def test_explicit_empty_array_remains_backward_compatible(extra):
    assert parse_bi_daily_aggregates({"results": [], **extra}) == []


@pytest.mark.parametrize("extra", [{}, {"status": "OK"}, {"status": "DELAYED"},
                                    {"resultsCount": 2}, {"queryCount": 2},
                                    {"resultsCount": 2, "queryCount": 10}])
def test_valid_bars_preserve_values_without_requiring_metadata(extra):
    payload = {"results": _bars(), **extra}
    before = copy.deepcopy(payload)
    assert parse_bi_daily_aggregates(payload) == before["results"]
    assert payload == before


def test_finite_fractional_timestamp_is_not_newly_rejected():
    bars = _bars()
    bars[0]["t"] += 0.25
    assert parse_bi_daily_aggregates({"results": bars}) == bars


@pytest.mark.parametrize("payload", [None, [], "PRIVATE", 0, False])
def test_non_object_payload_is_not_a_valid_empty_response(payload):
    _error(payload, "invalid_payload")


@pytest.mark.parametrize("payload", [{}, {"status": "OK"}, {"resultsCount": 0},
                                      {"queryCount": 0},
                                      {"status": "OK", "queryCount": 0}])
def test_omitted_results_requires_explicit_success_and_zero_result_count(payload):
    _error(payload, "missing_results")


@pytest.mark.parametrize("status", ["ERROR", "NOT_AUTHORIZED", "RATE_LIMITED", "PRIVATE"])
def test_provider_error_status_does_not_become_empty_success(status):
    _error({"status": status, "results": [], "error": "PRIVATE"}, "provider_status")


@pytest.mark.parametrize("value", [None, {}, (), "PRIVATE", 0, False])
def test_present_results_must_be_a_list(value):
    _error({"status": "OK", "results": value}, "invalid_results_type")


@pytest.mark.parametrize("field,reason", [("resultsCount", "invalid_result_count"),
                                          ("queryCount", "invalid_query_count")])
@pytest.mark.parametrize("value", [None, False, True, -1, 0.0, 2.0, "0", [], {}])
def test_present_counts_are_exact_nonnegative_integers(field, reason, value):
    _error({"results": [], field: value}, reason)


@pytest.mark.parametrize("bars,count", [([], 1), (_bars(), 0), (_bars(), 1), (_bars(), 3)])
def test_result_count_must_equal_returned_bar_count(bars, count):
    _error({"results": bars, "resultsCount": count}, "result_count_mismatch")


@pytest.mark.parametrize("count", [0, 1])
def test_query_count_cannot_be_smaller_than_returned_bars(count):
    _error({"results": _bars(), "queryCount": count}, "invalid_query_count")


@pytest.mark.parametrize("with_array", [False, True])
def test_empty_response_with_positive_query_count_is_contradictory(with_array):
    payload = {"status": "OK", "resultsCount": 0, "queryCount": 1}
    if with_array:
        payload["results"] = []
    _error(payload, "contradictory_empty_response")


@pytest.mark.parametrize("empty", [False, True])
def test_truncated_daily_history_cannot_claim_success(empty):
    payload = {"status": "OK", "results": [] if empty else _bars(),
               "next_url": "https://provider.invalid/PRIVATE?apiKey=PRIVATE"}
    _error(payload, "unexpected_pagination")


@pytest.mark.parametrize("value", [None, [], "PRIVATE", 1, False])
def test_bar_must_be_an_object(value):
    _error({"results": [value]}, "invalid_bar_type")


@pytest.mark.parametrize("key", ["t", "o", "h", "l", "c", "v"])
def test_every_required_bar_field_must_exist(key):
    bar = _bars()[0]
    del bar[key]
    _error({"results": [bar]}, "invalid_bar_value")


@pytest.mark.parametrize("key", ["t", "o", "h", "l", "c", "v"])
@pytest.mark.parametrize("value", [None, True, False, "10", float("nan"),
                                   float("inf"), -float("inf"), 10 ** 400])
def test_bar_values_are_finite_numeric_and_never_boolean(key, value):
    bar = _bars()[0]
    bar[key] = value
    _error({"results": [bar]}, "invalid_bar_value")


@pytest.mark.parametrize("key,value", [("o", 0), ("h", 0), ("l", 0), ("c", 0),
                                        ("o", -1), ("h", -1), ("l", -1), ("c", -1),
                                        ("v", -1)])
def test_prices_positive_and_volume_nonnegative(key, value):
    bar = _bars()[0]
    bar[key] = value
    _error({"results": [bar]}, "invalid_bar_value")


@pytest.mark.parametrize("change", [{"h": 10}, {"l": 11}, {"h": 8}, {"l": 13}])
def test_ohlc_geometry_must_be_physically_possible(change):
    bar = _bars()[0]
    bar.update(change)
    _error({"results": [bar]}, "invalid_bar_geometry")


@pytest.mark.parametrize("kind", ["zero", "negative", "duplicate", "descending"])
def test_timestamps_are_positive_and_strictly_ascending(kind):
    bars = _bars()
    if kind == "zero":
        bars[0]["t"] = 0
    elif kind == "negative":
        bars[0]["t"] = -1
    elif kind == "duplicate":
        bars[1]["t"] = bars[0]["t"]
    else:
        bars.reverse()
    _error({"results": bars}, "invalid_bar_timestamp")


class _Reply:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _lifecycle(monkeypatch, tmp_path, result, direction, responses):
    # This fixture imports modules.scanners, not api.py; every network call and
    # the only filesystem writes are respectively mocked and under tmp_path.
    import modules.scanners as scanners
    from test_bi_diagnostics_integration import _io

    tickers, snapshots = _io(monkeypatch, tmp_path, result, count=len(responses))
    final = tmp_path / (direction + ".json")
    final.write_text('{"results":[{"previous":true}]}', encoding="utf-8")
    before = final.read_bytes()
    # Re-fetching a malformed history must repeat the same ticker's response,
    # not accidentally borrow the following ticker's valid history.
    response_by_ticker = dict(zip(tickers, responses))
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        ticker = args[0].split('/ticker/', 1)[1].split('/', 1)[0]
        return _Reply(response_by_ticker[ticker])

    monkeypatch.setattr(scanners, "rate_limited_get", get)
    analyses = []

    def analyze(bars, direction="long"):
        analyses.append({"direction": direction, "bar_count": len(bars)})
        return result

    monkeypatch.setattr(scanners, "analyze_breakout_imminent", analyze)
    return scanners, tickers, final, before, calls, analyses, snapshots


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("with_array", [False, True])
@pytest.mark.parametrize("green,hard,expected", [(16, (), 0), (17, (), 1),
                                                (17, ("range_breakdown",), 0)])
def test_empty_then_analyzed_finishes_with_unchanged_strict_contract(
    monkeypatch, tmp_path, direction, with_array, green, hard, expected
):
    from test_bi_deep_fixes_scan import _flat_bars, _to_polygon
    from test_bi_diagnostics_integration import _result

    empty = {"status": "OK", "resultsCount": 0, "queryCount": 0}
    if with_array:
        empty["results"] = []
    bars = _to_polygon(_flat_bars())
    valid = {"status": "OK", "results": bars, "resultsCount": len(bars), "queryCount": len(bars)}
    scanners, tickers, final, before, calls, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(green, hard=hard), direction, [empty, valid]
    )
    scanners._bi_background_scan("fixture", direction, tickers)
    assert calls == [1, 1]
    assert analyses == [{"direction": direction, "bar_count": 50}]
    cache = json.loads(final.read_text())
    progress = json.loads((tmp_path / (direction + "-progress.json")).read_text())
    funnel = cache["diagnostics"]
    assert final.read_bytes() != before
    assert progress["status"] == "done"
    assert funnel["coverage"] == "complete"
    assert funnel["checked"] == funnel["total"] == 2
    assert funnel["history_available"] == funnel["analyzed"] == 1
    assert funnel["data_failures"] == funnel["analysis_errors"] == 0
    assert funnel["rejected"]["insufficient_daily_history"] == 1
    assert funnel["final_results"] == cache["count"] == len(cache["results"]) == expected
    assert funnel["confluence"]["evaluated"] == 1
    assert funnel["confluence"]["green_count_histogram"][str(green)] == 1
    assert progress["no_data"] == 1
    if expected == 0:
        assert funnel["rejected"]["indicator_or_hard_gate_contract"] == 1
    else:
        row = cache["results"][0]
        assert row["BI_IndicatorsGreen"] == row["BI_IndicatorsRequired"] == 17
        assert row["BI_IndicatorsAvailable"] == row["BI_IndicatorsTotal"] == 20
        assert row["BI_IndicatorContractVersion"] == scanners.BI_STOCK_CONTRACT_VERSION


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("invalid,reason", [
    ({"status": "OK"}, "missing_results"),
    ({"results": "PRIVATE"}, "invalid_results_type"),
    ({"results": [], "queryCount": 1}, "contradictory_empty_response"),
    ({"results": [], "next_url": "https://provider.invalid/PRIVATE"}, "unexpected_pagination"),
    ({"results": [None]}, "invalid_bar_type"),
    (_unconvertible_history(), "invalid_data_conversion"),
    (ValueError("PRIVATE JSON body"), "invalid_json"),
])
def test_empty_then_invalid_preserves_final_and_does_not_claim_zero_success(
    monkeypatch, tmp_path, direction, invalid, reason, capsys
):
    from test_bi_diagnostics_integration import _result

    empty = {"status": "OK", "resultsCount": 0, "queryCount": 0}
    scanners, tickers, final, before, calls, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), direction, [empty, invalid]
    )
    isolated = reason in {"invalid_bar_type", "invalid_data_conversion"}
    code = "scan_data_incomplete" if isolated else "scan_data_invalid"
    with pytest.raises(scanners.ScannerDataError, match=code) as caught:
        scanners._bi_background_scan("fixture", direction, tickers)
    assert final.read_bytes() == before
    assert calls == [1, 1] and analyses == []
    progress = json.loads((tmp_path / (direction + "-progress.json")).read_text())
    funnel = progress["diagnostics"]
    assert progress["status"] == "error" and progress["no_data"] == 1
    assert funnel["coverage"] == "incomplete" and funnel["final_results"] is None
    assert funnel["checked"] == funnel["total"] == 2
    assert funnel["data_failures"] == 1 and funnel["analyzed"] == 0
    assert funnel["rejected"] == {"insufficient_daily_history": 1}
    assert funnel["data_error_reason"] == reason
    assert "PRIVATE" not in json.dumps(progress)
    assert "PRIVATE" not in str(caught.value)
    assert "PRIVATE" not in "".join(traceback.format_exception(caught.value))
    assert "PRIVATE" not in capsys.readouterr().out
    assert not (tmp_path / (direction + ".json.partial")).exists()
