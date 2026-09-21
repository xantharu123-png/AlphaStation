"""Cup runtime admission preserves ranking and fail-closed publication, offline."""
from copy import deepcopy
import json

import pytest

import api
from test_stock_momentum_confirmed_contract import NOW, _wrapper_fixture


NAME = "Cup and Handle Breakout"


def _cup_fixture(monkeypatch, *, count=225, varied=False, rejected=()):
    post_filter = api._apply_special_strategy_post_filter
    writes = _wrapper_fixture(monkeypatch, price=102.)
    monkeypatch.setattr(api, "_apply_special_strategy_post_filter", post_filter)
    bars = [{"date": "2026-09-04", "open": 100., "high": 103., "low": 99.,
             "close": 102., "volume": 1_000_000, "fixture_index": i} for i in range(200)]
    snapshots = []
    ranks = {}
    for i in range(count):
        symbol = f"T{i:03d}"
        price = 102. + ((i * 17) % 29) / 100. if varied else 102.
        ranks[symbol] = price
        snapshots.append({"ticker": symbol,
            "day": {"o": 98., "h": price, "l": 96., "c": price, "v": 3_000_000},
            "prevDay": {"c": 98., "v": 1_000_000},
            "lastTrade": {"p": price, "t": int(NOW.timestamp()*1_000_000_000)},
            "lastQuote": {"p": price-.0123, "P": price+.0456}})
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: snapshots)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: (set(ranks), "fixture"))
    monkeypatch.setattr(api, "_stock_completed_pattern_history", lambda value, **kw: value)
    history_calls, native_calls, plan_calls, cup_calls, partials, attempts = [], [], [], [], [], []

    def history(symbol, minimum, *args):
        history_calls.append((symbol, minimum))
        return bars

    def execution(symbol, **kw):
        native_calls.append(symbol)
        return []

    def plan(*args, **kw):
        plan_calls.append((args, kw))
        # An unchanged native rejection does not remove/refill its generic slot.
        kw["diagnostics"].update(status="unavailable", reason="fixture_no_structure")
        return None

    def cup(row, strat):
        assert "_deferred_native_plan" not in row
        assert row["native_plan_reason"] == "fixture_no_structure"
        cup_calls.append(row["ticker"])
        if row["ticker"] in rejected:
            return None
        # This test stubs pattern recognition to measure admission/ranking,
        # not morphology. Model the new successful-detector receipt too.
        return dict(row, cup_pattern_version=api.CUP_PATTERN_CONTRACT_VERSION,
                    pattern_timeframe="1D", cup_rim_level=101.,
                    Breakout_Level=101., cup_confirmation_level=101., cup_confirmation_close=102.)

    monkeypatch.setattr(api, "_fetch_strategy_daily_history", history)
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", execution)
    monkeypatch.setattr(api, "_build_structured_trade_setup", plan)
    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", cup)
    monkeypatch.setattr(api, "_score_strategy_candidate", lambda **kw:
                        (80, {"direction": "long", "atr_pct": 3.}))
    monkeypatch.setattr(api, "save_partial_cache_file", lambda path, rows, **kw:
                        partials.append(deepcopy((rows, kw))))
    monkeypatch.setattr(api, "_publish_stock_strategy_attempt", lambda attempt, status, **kw:
                        attempts.append(deepcopy((status, {**kw, "error": str(kw.get("error", ""))}))))
    return {"writes": writes, "bars": bars, "ranks": ranks, "history": history_calls,
            "native": native_calls, "plans": plan_calls, "cup": cup_calls,
            "partials": partials, "attempts": attempts}


@pytest.mark.parametrize("varied", [False, True])
def test_cup_native_work_only_for_same_stable_top_180(monkeypatch, varied):
    state = _cup_fixture(monkeypatch, varied=varied)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    # Scores tie here; change is the existing secondary key, followed by stable
    # snapshot order. Incremental eviction must preserve both tie contracts.
    admitted = sorted(state["ranks"], key=lambda key: -state["ranks"][key])[:180]
    assert state["native"] == state["cup"] == admitted
    assert len(state["plans"]) == 180  # Previously 225 native/4H calls.
    assert len([c for c in state["history"] if c[1] == 70]) == 225
    assert len([c for c in state["history"] if c[1] == 180]) == 180
    assert [row["ticker"] for row in rows] == admitted[:50]
    diagnostics = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diagnostics["raw_matches_before_special_filter"] == 225
    assert diagnostics["special_filter_input_count"] == 225
    assert diagnostics["special_filter_checked_count"] == 180
    assert diagnostics["special_filter_unexamined_count"] == 45
    assert diagnostics["special_filter_limit"] == 180
    assert diagnostics["plan_build_counts"] == {"fixture_no_structure": 180}
    assert state["partials"] and all(not p[0] for p in state["partials"])
    public = json.dumps([rows, state["writes"], state["partials"], state["attempts"]], default=str)
    assert "_deferred_native_plan" not in public
    assert "fixture_index" not in public


def test_special_rejection_does_not_refill_old_ranked_slots(monkeypatch):
    rejected = {f"T{i:03d}" for i in range(180)}
    state = _cup_fixture(monkeypatch, rejected=rejected)
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert state["cup"] == [f"T{i:03d}" for i in range(180)]
    assert len(state["native"]) == 180
    assert not any(name in state["cup"] for name in ("T180", "T224"))
    diagnostics = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diagnostics["special_filter_checked_count"] == 180
    assert diagnostics["special_filter_unexamined_count"] == 45


@pytest.mark.parametrize("failure,expected", [
    (api.stock_scan_runtime.ScanWorkTimeout(), "scan_timeout"),
    (api.ScannerDataError("scan_data_invalid"), "scan_data_invalid"),
    (ValueError("private native details"), "scan_data_incomplete"),
])
def test_selected_native_failure_never_commits_or_refills(monkeypatch, tmp_path, failure, expected):
    state = _cup_fixture(monkeypatch)
    old = tmp_path / "cup-final.json"
    old.write_bytes(b'{"results":[{"ticker":"OLD"}],"partial":false}')
    before = old.read_bytes()

    def forbidden_final(*args, **kwargs):
        old.write_bytes(b"unexpected overwrite")
        pytest.fail("incomplete scan must not finalize")

    def fail_third(symbol, **kw):
        state["native"].append(symbol)
        if len(state["native"]) == 3:
            raise failure
        return []

    monkeypatch.setattr(api, "finalize_cache_file", forbidden_final)
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", fail_third)
    with pytest.raises((api.stock_scan_runtime.ScanWorkTimeout, api.ScannerDataError), match=expected):
        api._strategy_scan_wrapper(NAME, send_email=False)
    assert old.read_bytes() == before
    assert state["native"] == ["T000", "T001", "T002"]
    assert state["cup"] == ["T000", "T001"]
    assert state["attempts"][-1][0] == "error"
    diag = state["attempts"][-1][1]["diagnostics"]
    assert diag["special_filter_checked_count"] == 2
    assert diag["special_filter_unexamined_count"] == 223
    assert diag["coverage"] == "incomplete"
    assert all(not rows for rows, _ in state["partials"])
    assert "_deferred_native_plan" not in json.dumps(state["attempts"], default=str)


def test_insufficient_special_history_is_checked_not_unexamined(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    original = api._fetch_strategy_daily_history
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda symbol, minimum, *args:
                        [] if minimum == 180 else original(symbol, minimum, *args))
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert len(state["native"]) == 3
    assert state["cup"] == []
    diag = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diag["special_filter_checked_count"] == 3
    assert diag["special_filter_unexamined_count"] == 0


def test_no_candidates_has_explicit_zero_special_coverage(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    monkeypatch.setattr(api, "_stock_previous_session_change", lambda *a, **kw: -99.)
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    assert not state["native"] and not state["cup"]
    diag = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diag["special_filter_input_count"] == diag["special_filter_checked_count"] == 0
    assert diag["special_filter_unexamined_count"] == 0
    assert diag["special_filter_limit"] == 180
