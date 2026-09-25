"""Manual admission must not turn a blocked start into an old failed run.

Compile the real admission/ACK functions only. No app startup, credentials,
network, real threads, cache reads/writes or scanner callbacks are involved.
"""

import ast
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from typing import Any, Dict, Optional

import pytest


CUP = "strat_cup_and_handle_breakout"


@pytest.fixture(scope="module")
def admission_code():
    path = Path(__file__).with_name("api.py")
    wanted = {
        "_is_stock_strategy_worker", "_is_heavy_stock_worker", "_run_scan_safe",
        "_manual_scan_blocking_owner_locked", "_manual_scan_ack",
    }
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in nodes} == wanted
    return compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec")


class ThreadProbe:
    def __init__(self, target=None, daemon=True, *, alive=False):
        self.alive = alive
        self.target = target
        self.starts = 0

    def is_alive(self):
        return self.alive

    def start(self):
        self.starts += 1
        self.alive = True
        # Do not execute the scanner worker in this admission-only test.


@pytest.fixture
def scope(admission_code):
    state = {
        CUP: {"running": False, "last_run_id": "old-failed-cup",
              "last_attempt_at": "old-cup-attempt", "last_error": "scan_timeout"},
    }
    ns = dict(
        Any=Any, Dict=Dict, Optional=Optional, datetime=datetime,
        uuid=SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="new-run")),
        time=SimpleNamespace(time=lambda: 1234.0),
        threading=SimpleNamespace(Thread=ThreadProbe),
        _scan_lock=Lock(), _scan_status=state, _scan_threads={}, _SCAN_TIMEOUTS={},
        _scan_control_supported=lambda _name: False,
        _scan_runtime_state=lambda *_args, **_kwargs: {"timeout_exceeded": False},
    )
    exec(admission_code, ns)
    return ns


def attempt(scope, name=CUP):
    calls = []
    accepted = scope["_run_scan_safe"](name, lambda: calls.append("executed"))
    assert calls == []
    return accepted


def assert_busy(ack, owner=None):
    assert ack["status"] == "busy"
    assert ack["accepted"] is False
    assert ack["run_id"] is None
    assert ack["last_attempt_at"] is None
    assert ack["blocking_scan_key"] == owner
    assert ack["reason"] == ("other_scanner_running" if owner else "start_not_accepted")


@pytest.mark.parametrize("owner", ["biotech", "strategy_scan", "bi_long", "bi_short",
                                    "strat_momentum_breakout_long"])
@pytest.mark.parametrize("running,alive", [(True, True), (True, False), (False, True)])
def test_cup_blocked_by_other_stock_owner_never_follows_old_cup(scope, owner, running, alive):
    scope["_scan_status"][owner] = {"running": running, "last_run_id": "owner-run"}
    scope["_scan_threads"][owner] = ThreadProbe(alive=alive)
    before = deepcopy(scope["_scan_status"])
    accepted = attempt(scope)
    assert accepted is False
    ack = scope["_manual_scan_ack"](CUP, accepted, strategy="Cup and Handle Breakout")
    assert_busy(ack, owner)
    assert ack["strategy"] == "Cup and Handle Breakout"
    assert scope["_scan_status"] == before
    assert CUP not in scope["_scan_threads"]


@pytest.mark.parametrize("requested,owner", [
    ("crypto_explosion", "crypto_trade_signals"),
    ("crypto_trade_signals", "crypto_explosion"),
    ("crypto_trade_signals", "new_listing"),
    ("new_listing", "crypto_trade_signals"),
])
@pytest.mark.parametrize("running,alive", [(True, False), (False, True)])
def test_crypto_shared_owner_has_same_honest_busy_contract(scope, requested, owner, running, alive):
    scope["_scan_status"][requested] = dict(scope["_scan_status"][CUP])
    scope["_scan_status"][owner] = {"running": running}
    scope["_scan_threads"][owner] = ThreadProbe(alive=alive)
    accepted = attempt(scope, requested)
    assert accepted is False
    assert_busy(scope["_manual_scan_ack"](requested, accepted), owner)


@pytest.mark.parametrize("running", [False, True])
def test_own_live_worker_can_be_followed_even_during_finalization(scope, running):
    scope["_scan_status"][CUP].update(running=running, last_run_id="own-live-run")
    scope["_scan_threads"][CUP] = ThreadProbe(alive=True)
    accepted = attempt(scope)
    assert accepted is False
    ack = scope["_manual_scan_ack"](CUP, accepted)
    assert ack["status"] == "already_running"
    assert ack["accepted"] is False
    assert ack["run_id"] == "own-live-run"


@pytest.mark.parametrize("has_dead_thread", [False, True])
def test_own_flag_without_a_live_worker_does_not_prove_a_followable_run(scope, has_dead_thread):
    scope["_scan_status"][CUP]["running"] = True
    if has_dead_thread:
        scope["_scan_threads"][CUP] = ThreadProbe(alive=False)
    accepted = attempt(scope)
    assert accepted is False
    assert_busy(scope["_manual_scan_ack"](CUP, accepted))


def test_other_owner_finishing_before_ack_cannot_fabricate_own_running_state(scope):
    scope["_scan_status"]["biotech"] = {"running": True}
    scope["_scan_threads"]["biotech"] = ThreadProbe(alive=True)
    accepted = attempt(scope)
    assert accepted is False
    scope["_scan_status"]["biotech"]["running"] = False
    scope["_scan_threads"].pop("biotech")
    assert_busy(scope["_manual_scan_ack"](CUP, accepted))


def test_own_worker_finishing_before_ack_does_not_replay_stale_failure(scope):
    scope["_scan_status"][CUP]["running"] = True
    scope["_scan_threads"][CUP] = ThreadProbe(alive=True)
    accepted = attempt(scope)
    assert accepted is False
    scope["_scan_status"][CUP]["running"] = False
    scope["_scan_threads"].pop(CUP)
    assert_busy(scope["_manual_scan_ack"](CUP, accepted))


def test_failed_expected_run_guard_is_not_a_running_scan(scope):
    accepted = scope["_run_scan_safe"](CUP, lambda: pytest.fail("must not scan"),
                                        expected_previous_run_id="not-old-failed-cup")
    assert accepted is False
    assert_busy(scope["_manual_scan_ack"](CUP, accepted))


@pytest.mark.parametrize("already_finished", [False, True])
def test_successful_admission_is_started_even_if_worker_finishes_before_ack(scope, already_finished):
    accepted = attempt(scope)
    assert accepted is True
    assert scope["_scan_threads"][CUP].starts == 1
    if already_finished:
        scope["_scan_threads"].pop(CUP)
        scope["_scan_status"][CUP]["running"] = False
    ack = scope["_manual_scan_ack"](CUP, accepted, message="requested scan started")
    assert ack["status"] == "started"
    assert ack["accepted"] is True
    assert ack["run_id"] == "new-run"
    assert ack["message"] == "requested scan started"


def test_unrelated_worker_is_not_reported_as_a_blocking_owner(scope):
    scope["_scan_status"]["crypto_explosion"] = {"running": True}
    scope["_scan_threads"]["crypto_explosion"] = ThreadProbe(alive=True)
    assert_busy(scope["_manual_scan_ack"](CUP, False))
