"""A single retry reuses only unused, same-sweep budget and immutable data."""
import threading
from types import SimpleNamespace

import pytest

from modules import scan_control
from modules import stock_scan_runtime as runtime


@pytest.fixture
def clock(monkeypatch):
    value = {"now": 100.0}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: value["now"]))
    monkeypatch.setattr(runtime, "LEAF_WORK_SECONDS", 1200)
    monkeypatch.setattr(runtime, "SWEEP_WORK_SECONDS", 1800)
    monkeypatch.setattr(runtime, "_PROGRESS", {})
    return value


def _timeout_first(clock, sweep, name="Momentum Breakout Long"):
    sweep["remaining_leaves"] = 4
    with pytest.raises(runtime.ScanWorkTimeout):
        with runtime.scope(name) as leaf:
            runtime.cache_put("completed-history", {"close": [10., 11.]})
            leaf["private_partial_results"] = [{"ticker": "DO_NOT_REUSE"}]
            clock["now"] = leaf["deadline"]
            runtime.checkpoint("history")
    return leaf


def test_retry_uses_649_unused_seconds_after_realistic_siblings_and_same_cache(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        first = _timeout_first(clock, sweep)
        assert clock["now"] == 550.
        root = sweep["root"]
        original_deadline = root["work_deadline"]
        for name in ("Gap Momentum Long", "Gap Momentum Short", "Cup and Handle Breakout"):
            with runtime.scope(name):
                assert runtime.current()["root"] is root
        clock["now"] = 1236.  # 1136 seconds used; 664 remain from the unchanged 30 minutes.
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True
        with runtime.scope("Momentum Breakout Long") as retried:
            assert retried is not first and retried["is_timeout_retry"] is True
            assert retried["stage_elapsed_seconds"] == {}
            assert "private_partial_results" not in retried
            assert retried["root"] is root
            assert retried["deadline"] - clock["now"] == 649.
            assert root["work_deadline"] == original_deadline == 1900.
            history = runtime.cache_get("completed-history")
            history["close"][0] = 999.
            assert runtime.cache_get("completed-history")["close"][0] == 10.
            clock["now"] += 600.
            runtime.checkpoint("publish")
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False
    assert runtime.current() is None
    with runtime.scope("new-sweep", sweep=True) as other:
        other["remaining_leaves"] = 0
        assert runtime.cache_get("completed-history") is None
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False


def test_only_one_retry_is_claimed_across_all_timed_out_strategies(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep, "first")
        _timeout_first(clock, sweep, "second")
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("first") is True
        assert runtime.claim_sweep_timeout_retry("second") is False
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.scope("first") as retried:
                clock["now"] = retried["deadline"]
                runtime.checkpoint()
        assert runtime.claim_sweep_timeout_retry("first") is False


@pytest.mark.parametrize("remaining", [None, 1, 4, -1, False, "0"])
def test_cannot_retry_before_explicitly_finishing_initial_siblings(clock, remaining):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = remaining
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False


@pytest.mark.parametrize("usable,expected", [(-1., False), (0., False), (59.999, False), (60., True)])
def test_minimum_usable_budget_is_measured_after_finishing_reserve(clock, usable, expected):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = 0
        clock["now"] = sweep["root"]["work_deadline"] - 15. - usable
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is expected
        assert sweep["root"]["work_deadline"] == 1900.


@pytest.mark.parametrize("failure", [RuntimeError("scan_timeout"), ValueError("provider failed"),
                                     scan_control.ScanRestartRequired(), KeyboardInterrupt(), SystemExit()])
def test_non_work_timeout_and_control_exceptions_propagate_and_are_not_retryable(clock, failure):
    with runtime.scope("sweep", sweep=True) as sweep:
        with pytest.raises(type(failure)) as caught:
            with runtime.scope("Momentum Breakout Long"):
                raise failure
        assert caught.value is failure
        assert runtime.current() is sweep
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False


def test_claim_is_owner_only_not_leaf_nested_sweep_manual_or_other_thread(clock):
    assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False
    with runtime.scope("manual") as manual:
        manual["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("manual") is False
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = 0
        with runtime.scope("child") as leaf:
            leaf["remaining_leaves"] = 0
            assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False
        with runtime.scope("nested-sweep", sweep=True) as nested:
            nested["remaining_leaves"] = 0
            assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False
        claims = []
        worker = threading.Thread(target=lambda: claims.append(runtime.claim_sweep_timeout_retry("Momentum Breakout Long")))
        worker.start()
        worker.join(timeout=2)
        assert claims == [False]
        assert runtime.claim_sweep_timeout_retry("never-timed-out") is False
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True


def test_retry_cannot_begin_after_finishing_reserve_even_if_claim_was_earlier(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True
        clock["now"] = sweep["root"]["work_deadline"] - 15.
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.scope("Momentum Breakout Long"):
                pytest.fail("No retry body after its deadline")
        assert runtime.current() is sweep
        runtime.checkpoint("mail_guard")  # Existing delivery-owner semantics are unchanged.


def test_retry_keeps_leaf_cap_and_completed_pause_exclusion(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True
        with runtime.scope("Momentum Breakout Long") as retried:
            assert retried["deadline"] - clock["now"] == 1200.
            deadline = retried["deadline"]
            root_deadline = sweep["root"]["work_deadline"]
            clock["now"] += 500.
            runtime.exclude_pause(500.)
            assert retried["deadline"] == deadline + 500.
            assert sweep["root"]["work_deadline"] == root_deadline + 500.
            assert runtime.diagnostics()["leaf_elapsed_seconds"] == 0
            clock["now"] = retried["deadline"]
            with pytest.raises(runtime.ScanWorkTimeout):
                runtime.checkpoint()


def test_pause_before_retry_claim_preserves_only_original_active_time(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        clock["now"] = 1236.
        clock["now"] += 500.
        runtime.exclude_pause(500.)
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True
        with runtime.scope("Momentum Breakout Long") as retried:
            assert retried["deadline"] - clock["now"] == 649.
            assert runtime.diagnostics()["elapsed_seconds"] == 1136


def test_retry_network_timeout_and_late_result_obey_reserved_deadline(clock):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        clock["now"] = 1236.
        sweep["remaining_leaves"] = 0
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is True
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.scope("Momentum Breakout Long") as retried:
                clock["now"] = retried["deadline"] - 2.
                assert runtime.request_timeout((15., 3.)) == (2., 2.)
                clock["now"] += 3.  # An in-flight call returned after the work deadline.
                runtime.checkpoint("publish")
        assert sweep["root"]["work_deadline"] - clock["now"] == 14.


def test_retry_minimum_usable_work_also_respects_existing_leaf_cap(clock, monkeypatch):
    with runtime.scope("sweep", sweep=True) as sweep:
        _timeout_first(clock, sweep)
        sweep["remaining_leaves"] = 0
        monkeypatch.setattr(runtime, "LEAF_WORK_SECONDS", 59.)
        assert runtime.claim_sweep_timeout_retry("Momentum Breakout Long") is False
