"""Offline integration of one cached, bounded retry into the stock sweep."""
from copy import deepcopy
import json

import pytest

import api
from modules import stock_scan_runtime as runtime
from test_stock_strategy_sweep_isolation import (
    STRATEGIES, CODES, _mock_sweep, _offline_sweep_state, _row,
)


def _cached_retry_sweep(monkeypatch, tmp_path, *, rows_per_strategy=1):
    cache, previous, observed = _mock_sweep(monkeypatch, tmp_path, {})
    clock = [1000.0]
    monkeypatch.setattr(runtime.time, "monotonic", lambda: clock[0])
    outcomes = {
        name: [_row(f"S{i}R{j}", score=1000 * i - j) for j in range(rows_per_strategy)]
        for i, name in enumerate(STRATEGIES)
    }
    root_ids, deadlines = [], []
    first = [True]

    @runtime.bounded_leaf
    def scan(name, send_email=True, *, publish_generic_cache=True):
        assert send_email is False and publish_generic_cache is False
        observed["attempts"].append(name)
        state = runtime.current()
        root_ids.append(id(state["root"]))
        deadlines.append(state["deadline"])
        assert state["root"]["work_deadline"] == 2800.0
        if name == STRATEGIES[0]:
            if first[0]:
                first[0] = False
                runtime.cache_put(("daily_history", "EXAMPLE", "session"), [{"close": 100}])
                clock[0] = state["deadline"]
                runtime.checkpoint("history")
                pytest.fail("first fair-share timeout must propagate")
            assert state["is_timeout_retry"] is True
            assert runtime.cache_get(("daily_history", "EXAMPLE", "session")) == [{"close": 100}]
            clock[0] += 300
        else:
            assert state["is_timeout_retry"] is False
            # A sibling is free to enrich its copy without poisoning retry data.
            row = runtime.cache_get(("daily_history", "EXAMPLE", "session"))
            row[0]["close"] = -1
            clock[0] += {STRATEGIES[1]: 14, STRATEGIES[2]: 10, STRATEGIES[3]: 662}[name]
        runtime.checkpoint("publish")
        return deepcopy(outcomes[name])

    monkeypatch.setattr(api, "_strategy_scan_wrapper", scan)
    return cache, observed, clock, root_ids, deadlines, outcomes


def test_real_timeout_retry_recovers_without_new_budget_or_duplicate_mail(monkeypatch, tmp_path):
    cache, observed, clock, roots, deadlines, outcomes = _cached_retry_sweep(monkeypatch, tmp_path)
    api._stock_strategy_alert_sweep_wrapper()
    assert observed["attempts"] == list(STRATEGIES) + [STRATEGIES[0]]
    assert len(set(roots)) == 1
    assert deadlines[0] == 1450.0 and deadlines[-1] == 2785.0
    assert clock[0] == 2436.0  # 450 + 686 + 300, not a fresh 30-minute budget.
    assert runtime.current() is None
    payload = json.loads(cache.read_text())
    diag = payload["diagnostics"]
    assert (diag["strategies_attempted"], diag["strategies_completed"], diag["strategies_failed"]) == (4, 4, 0)
    assert (diag["timeout_retries_attempted"], diag["timeout_retries_recovered"]) == (1, 1)
    assert diag["coverage"] == "complete" and diag["final_results"] == 4
    assert diag["strategy_results"][CODES[0]] == {
        "status": "complete", "result_count": 1, "aggregate_candidate_count": 1,
        "timeout_retry_count": 1, "initial_error_code": "scan_timeout",
    }
    assert all("timeout_retry_count" not in diag["strategy_results"][code] for code in CODES[1:])
    assert len(observed["mail_calls"]) == 1
    assert observed["mail_calls"][0][1] == payload["results"]
    for row in payload["results"]:
        source = outcomes[row["Strategy"]][0]
        assert {key: row[key] for key in source} == source


def test_retry_keeps_same_global_rank_and_per_strategy_mail_limits(monkeypatch, tmp_path):
    cache, observed, *_ = _cached_retry_sweep(monkeypatch, tmp_path, rows_per_strategy=40)
    api._stock_strategy_alert_sweep_wrapper()
    payload = json.loads(cache.read_text())
    rows = payload["results"]
    assert len(rows) == 100
    assert all(sum(row["Strategy"] == strategy for row in rows) == 25 for strategy in STRATEGIES)
    assert len(observed["mail_calls"]) == 1
    assert observed["mail_calls"][0][1] == rows[:75]
    assert [row["score"] for row in rows] == sorted((row["score"] for row in rows), reverse=True)


@pytest.mark.parametrize("count,code", [(True, "scan_timeout"), (2, "scan_timeout"),
                                        (-1, "scan_timeout"), (1, "PRIVATE"), (1, None)])
def test_retry_metadata_projection_rejects_invalid_claims(count, code):
    diag = api._stock_strategy_attempt_diagnostics({"strategy_results": {
        CODES[0]: {"status": "error", "error_code": "scan_data_invalid",
                   "timeout_retry_count": count, "initial_error_code": code},
    }}, sweep=True)
    assert diag["strategy_results"][CODES[0]] == {
        "status": "error", "result_count": None, "error_code": "scan_data_invalid",
    }
