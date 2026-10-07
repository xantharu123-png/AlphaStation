"""A stock leaf's shared twenty-symbol exclusion budget is order independent."""
from copy import deepcopy

import pytest

import api
from modules import stock_swing_contract as swing
from test_cup_runtime_admission import NAME as CUP_NAME, _cup_fixture
from test_stock_history_isolation import local_error
from test_stock_momentum_confirmed_contract import NAME, NOW, _wrapper_fixture


def _mixed_fixture(monkeypatch, symbols, *, final_path=None, special_history_error=False):
    real_finalize = api.finalize_cache_file
    real_save = api.save_cache_file
    writes = (_cup_fixture(monkeypatch, count=len(symbols))["writes"]
              if special_history_error else _wrapper_fixture(monkeypatch))
    session, previous_session = swing.completed_sessions(NOW, 2)
    observations = {
        symbol: {
            "ticker": symbol,
            "day": {"o": 98., "h": 102., "l": 96., "c": 102., "v": 3_000_000},
            "prevDay": {"c": 98., "v": 1_000_000},
            **swing.metadata(session, 102.),
        }
        for symbol in symbols
    }
    attempts = []

    def history(symbol, minimum, *args):
        if symbol.startswith("H") or (special_history_error and symbol == "GOOD" and minimum == 180):
            raise local_error()
        current_close = 101.99 if symbol.startswith("R") else 102.
        return [
            {"date": previous_session, "open": 98., "high": 99., "low": 97.,
             "close": 98., "volume": 1_000_000},
            {"date": session, "open": 98., "high": 102., "low": 96.,
             "close": current_close, "volume": 3_000_000},
        ]

    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe",
                        lambda *args: list(observations.values()))
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", history)
    monkeypatch.setattr(api, "_publish_stock_strategy_attempt",
                        lambda attempt, status, **kwargs:
                        attempts.append((status, deepcopy(kwargs))))
    if final_path is not None:
        monkeypatch.setattr(api, "_strategy_cache_path", lambda *args: str(final_path))

        def finalize(path, rows, **kwargs):
            with pytest.MonkeyPatch.context() as cache_io:
                cache_io.setattr(api, "save_cache_file", real_save)
                real_finalize(path, rows, **kwargs)
            writes.append(deepcopy((rows, kwargs)))

        monkeypatch.setattr(api, "finalize_cache_file", finalize)
    return writes, attempts


@pytest.mark.parametrize("history_count, reference_count", [(19, 1), (1, 19)])
@pytest.mark.parametrize("reference_first", [False, True])
def test_twenty_mixed_exclusions_keep_valid_sibling(monkeypatch, reference_first, history_count, reference_count):
    local = [f"H{i:03d}" for i in range(history_count)]
    reference = [f"R{i:03d}" for i in range(reference_count)]
    symbols = (reference + local if reference_first else local + reference) + ["GOOD"]
    writes, attempts = _mixed_fixture(monkeypatch, symbols)

    rows = api._strategy_scan_wrapper(NAME, send_email=False)

    assert [row["ticker"] for row in rows] == ["GOOD"]
    diagnostics = writes[0][1]["metadata"]["diagnostics"]
    assert diagnostics["coverage"] == "complete_with_exclusions"
    assert diagnostics["excluded_data_symbols"] == 20
    assert diagnostics["invalid_history_symbols"] == history_count
    assert diagnostics["daily_reference_exclusions"] == reference_count
    assert diagnostics["rejected"]["invalid_daily_history"] == history_count
    assert diagnostics["rejected"]["daily_reference:reference_price_mismatch"] == reference_count
    assert attempts[-1][0] == "complete"
    projected = api._stock_strategy_attempt_diagnostics(attempts[-1][1]["diagnostics"], sweep=False)
    assert projected["daily_reference_exclusions"] == reference_count


@pytest.mark.parametrize("history_count, reference_count", [(20, 1), (1, 20), (20, 20)])
@pytest.mark.parametrize("reference_first", [False, True])
def test_more_than_twenty_mixed_exclusions_fail_without_overwriting_final_cache(
    monkeypatch, tmp_path, reference_first, history_count, reference_count,
):
    local = [f"H{i:03d}" for i in range(history_count)]
    reference = [f"R{i:03d}" for i in range(reference_count)]
    symbols = (reference + local if reference_first else local + reference) + ["GOOD"]
    final = tmp_path / "previous-final.json"
    final.write_bytes(b'{"results":[{"ticker":"PREVIOUS"}],"partial":false}')
    before = final.read_bytes()
    writes, attempts = _mixed_fixture(monkeypatch, symbols, final_path=final)
    failure = None

    try:
        api._strategy_scan_wrapper(NAME, send_email=False)
    except api.ScannerDataError as error:
        failure = error

    assert final.read_bytes() == before, "An over-budget leaf replaced the previous final cache"
    assert failure is not None and failure.code == "scan_data_invalid"
    assert writes == []
    assert attempts[-1][0] == "error"
    diagnostics = attempts[-1][1]["diagnostics"]
    assert diagnostics["coverage"] == "incomplete"
    assert diagnostics["final_results"] is None


@pytest.mark.parametrize("cohort", ["H", "R"])
def test_twenty_one_homogeneous_invalid_symbols_still_fail_leaf(monkeypatch, tmp_path, cohort):
    symbols = [f"{cohort}{i:03d}" for i in range(21)] + ["GOOD"]
    final = tmp_path / "previous-final.json"
    final.write_bytes(b'{"results":[{"ticker":"PREVIOUS"}],"partial":false}')
    before = final.read_bytes()
    writes, attempts = _mixed_fixture(monkeypatch, symbols, final_path=final)

    with pytest.raises(api.ScannerDataError, match="scan_data_invalid"):
        api._strategy_scan_wrapper(NAME, send_email=False)

    assert final.read_bytes() == before
    assert writes == []
    assert attempts[-1][0] == "error"
    diagnostics = attempts[-1][1]["diagnostics"]
    assert diagnostics["coverage"] == "incomplete"
    assert diagnostics["final_results"] is None
    if cohort == "H":
        assert diagnostics["invalid_history_symbols"] == 21
        assert diagnostics["excluded_data_symbols"] == 20
        assert diagnostics["stock_history_error_counts"]["symbol_exclusion_limit"] == 1
    else:
        assert diagnostics["rejected"]["daily_reference:reference_price_mismatch"] == 21


def test_special_filter_history_error_cannot_exceed_already_used_mixed_budget(monkeypatch, tmp_path):
    symbols = [f"H{i:03d}" for i in range(19)] + ["R000", "GOOD"]
    final = tmp_path / "previous-final.json"
    final.write_bytes(b'{"results":[{"ticker":"PREVIOUS"}],"partial":false}')
    before = final.read_bytes()
    writes, attempts = _mixed_fixture(
        monkeypatch, symbols, final_path=final, special_history_error=True,
    )
    failure = None

    try:
        api._strategy_scan_wrapper(CUP_NAME, send_email=False)
    except api.ScannerDataError as error:
        failure = error

    assert final.read_bytes() == before, "The special filter published after exhausting the shared budget"
    assert failure is not None and failure.code == "scan_data_invalid"
    assert writes == []
    assert attempts[-1][0] == "error"
    diagnostics = attempts[-1][1]["diagnostics"]
    assert diagnostics["coverage"] == "incomplete"
    assert diagnostics["final_results"] is None
    assert diagnostics["daily_reference_exclusions"] == 1
    assert diagnostics["invalid_history_symbols"] == 20
    assert diagnostics["excluded_data_symbols"] == 20


@pytest.mark.parametrize("value, expected", [(1, 1), (True, None), (-1, None), ("1", None)])
def test_reference_exclusion_counter_survives_only_safe_numeric_projection(value, expected):
    projected = api._stock_strategy_attempt_diagnostics(
        {"daily_reference_exclusions": value}, sweep=False,
    )
    assert projected.get("daily_reference_exclusions") == expected
