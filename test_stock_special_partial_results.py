"""Only fully checked Cup rows enter bounded, display-only live partial caches."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import api
from test_cup_runtime_admission import NAME, _cup_fixture


def _special_partials(state):
    return [(rows, metadata) for rows, metadata in state["partials"]
            if metadata["metadata"]["diagnostics"].get("runtime_phase") == "special_filter"]


def test_first_confirmed_cup_is_published_before_next_pattern_without_generic_candidates(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    original_filter = api._apply_cup_handle_strategy_filter
    observed = []

    def pattern(row, strat, **kwargs):
        if observed:
            assert any(rows and rows[0]["ticker"] == "T000" for rows, _ in _special_partials(state))
            assert not state["writes"]  # Still scanning, no completed-cache publication.
        observed.append(row["ticker"])
        return original_filter(row, strat, **kwargs)

    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", pattern)
    final = api._strategy_scan_wrapper(NAME, send_email=False)
    assert [row["ticker"] for row in final] == observed == ["T000", "T001", "T002"]
    assert all(not rows for rows, meta in state["partials"]
               if meta["metadata"]["diagnostics"].get("runtime_phase") != "special_filter")
    special = _special_partials(state)
    assert special[0][0] == []
    assert special[1][0][0]["ticker"] == "T000"
    assert len(special[-1][0]) == 3
    for rows, metadata in special:
        assert metadata["checked"] == metadata["total"] == 3
        diagnostic = metadata["metadata"]["diagnostics"]
        assert diagnostic["coverage"] == "incomplete" and diagnostic["scan_in_progress"] is True
        assert "Musterpruefung" in metadata["detail"]
        assert all(api._stock_momentum_row_contract_valid(row) for row in rows)
    assert "_deferred_native_plan" not in json.dumps(special, default=str)
    assert "fixture_index" not in json.dumps(special, default=str)


def test_special_progress_keeps_universe_and_admitted_pattern_counts_distinct(monkeypatch):
    state = _cup_fixture(monkeypatch, count=185)
    api._strategy_scan_wrapper(NAME, send_email=False)
    rows, metadata = _special_partials(state)[-1]
    diagnostic = metadata["metadata"]["diagnostics"]
    assert metadata["checked"] == metadata["total"] == diagnostic["universe_count"] == 185
    assert diagnostic["special_filter_input_count"] == 185
    assert diagnostic["special_filter_checked_count"] == 180
    assert diagnostic["special_filter_unexamined_count"] == 5
    assert "Musterpruefung 180/180 ausgewaehlte Kandidaten" in metadata["detail"]
    assert len(rows) == 50  # Same visible-result cap, no preview growth with admitted pool.


def test_progress_callback_does_not_change_final_ranking_rows_or_history(monkeypatch):
    post_filter = api._apply_special_strategy_post_filter
    with monkeypatch.context() as baseline_patch:
        baseline = _cup_fixture(baseline_patch, count=6, varied=True, rejected={"T002"})
        baseline_patch.setattr(api, "_apply_special_strategy_post_filter",
            lambda candidates, strat, name, diagnostics=None, **kwargs:
                post_filter(candidates, strat, name, diagnostics))
        expected = deepcopy(api._strategy_scan_wrapper(NAME, send_email=False))
        expected_bars = deepcopy(baseline["bars"])
        expected_calls = deepcopy(baseline["cup"])
    observed = _cup_fixture(monkeypatch, count=6, varied=True, rejected={"T002"})
    result = api._strategy_scan_wrapper(NAME, send_email=False)
    assert result == expected
    assert observed["bars"] == expected_bars and observed["cup"] == expected_calls
    assert _special_partials(observed)[-1][0] == result


def test_rejected_pattern_never_enters_preview_and_progress_advances(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3, rejected={"T001"})
    clock = {"now": 100.}
    monkeypatch.setattr(api.stock_scan_runtime, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(api.time, "monotonic", lambda: clock["now"])
    original = api._apply_cup_handle_strategy_filter

    def pattern(*args, **kwargs):
        clock["now"] += 2.
        return original(*args, **kwargs)

    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", pattern)
    api._strategy_scan_wrapper(NAME, send_email=False)
    partials = _special_partials(state)
    assert [meta["metadata"]["diagnostics"]["special_filter_checked_count"] for _, meta in partials] == [0, 1, 2, 3]
    assert [row["ticker"] for row in partials[2][0]] == ["T000"]
    assert all(row["ticker"] != "T001" for rows, _ in partials for row in rows)


def test_partial_writer_throttles_fast_candidates_but_flushes_first_and_last(monkeypatch):
    state = _cup_fixture(monkeypatch, count=10)
    monkeypatch.setattr(api.time, "monotonic", lambda: 100.)
    api._strategy_scan_wrapper(NAME, send_email=False)
    partials = _special_partials(state)
    assert [meta["metadata"]["diagnostics"]["special_filter_checked_count"] for _, meta in partials] == [0, 1, 10]


def test_invalid_cup_receipt_is_never_exposed_even_when_pattern_adapter_returns_row(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    original = api._apply_cup_handle_strategy_filter

    def invalid(*args, **kwargs):
        row = original(*args, **kwargs)
        row.pop("cup_pattern_version")
        return row

    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", invalid)
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert all(not rows for rows, _ in state["partials"])


def test_watch_state_remains_excluded_by_existing_signal_only_policy(monkeypatch):
    state = _cup_fixture(monkeypatch, count=2)
    original = api._apply_cup_handle_strategy_filter

    def watch(*args, **kwargs):
        row = original(*args, **kwargs)
        return dict(row, trade_action="LONG_WATCH", trade_signal="BEOBACHTEN", entry_status="WAIT_FOR_TRIGGER")

    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", watch)
    api._strategy_scan_wrapper(NAME, send_email=False)
    assert any(rows for rows, _ in _special_partials(state))
    for rows, _ in _special_partials(state):
        assert api._apply_signal_only_policy("strategy_scan", deepcopy(rows)) == []


@pytest.mark.parametrize("failure", [api.stock_scan_runtime.ScanWorkTimeout(), api.ScannerDataError("scan_data_invalid")])
def test_later_failure_removes_partial_without_finalizing_or_sending(monkeypatch, failure):
    state = _cup_fixture(monkeypatch, count=3)
    removed = []
    monkeypatch.setattr(api, "_remove_partial_cache", lambda path: removed.append(path))
    original = api._apply_cup_handle_strategy_filter

    def pattern(row, *args, **kwargs):
        if row["ticker"] == "T001":
            assert any(rows for rows, _ in _special_partials(state))
            raise failure
        return original(row, *args, **kwargs)

    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", pattern)
    with pytest.raises(type(failure)):
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert not state["writes"]
    assert len(removed) == 2  # Startup and terminal cleanup; no leftover failed partial.
    assert state["attempts"][-1][0] == "error"


def test_partial_publish_error_is_not_converted_to_complete_zero(monkeypatch):
    state = _cup_fixture(monkeypatch, count=2)
    original = api.save_partial_cache_file

    def fail_write(path, rows, **kwargs):
        if rows:
            raise OSError("synthetic partial write failure")
        original(path, rows, **kwargs)

    monkeypatch.setattr(api, "save_partial_cache_file", fail_write)
    with pytest.raises(OSError, match="synthetic partial"):
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert not state["writes"] and state["attempts"][-1][0] == "error"
