"""One pure policy keeps API capabilities and controller admission consistent."""
import threading

import pytest

from modules import scan_control, scan_control_policy


LEGACY = (
    "strategy_scan", "bi_long", "bi_short", "strat_",
    "strat_cup_and_handle_breakout", "strat_wyckoff_accumulation",
)
DEDICATED = (
    "biotech", "bear", "turtle", "orb", "penny_stocks", "volume_spikes",
    "money_flow", "early_movers", "btc_divergenz", "crypto_explosion",
)
CRYPTO_STRATEGIES = ("crypto_strat_", "crypto_strat_momentum", "crypto_strat_low_cap")
PROTECTED = {
    "new_listing": "mixed_position_management",
    "crypto_trade_signals": "mixed_position_management",
    "penny_positions": "protection_monitor",
    "cup_handle_watch": "protection_monitor",
    "quote_capability": "protection_monitor",
    "crash_monitor": "protection_monitor",
    "market_context": "protection_monitor",
}


@pytest.mark.parametrize("name", LEGACY)
def test_legacy_owners_keep_same_data_resume_policy(name):
    assert scan_control_policy.capability(name) == {
        "supported": True, "unsupported_reason": None,
        "protected": False, "resume_policy": "continue_if_valid",
    }


@pytest.mark.parametrize("name", DEDICATED + CRYPTO_STRATEGIES)
def test_dedicated_discovery_owners_require_fresh_restart(name):
    assert scan_control_policy.capability(name) == {
        "supported": True, "unsupported_reason": None,
        "protected": False, "resume_policy": "restart_fresh",
    }


def test_exported_dedicated_set_is_exact_and_immutable():
    assert isinstance(scan_control_policy.DEDICATED_SCANNERS, frozenset)
    assert scan_control_policy.DEDICATED_SCANNERS == frozenset(DEDICATED)


@pytest.mark.parametrize("name,reason", PROTECTED.items())
def test_protection_and_mixed_lifecycle_owners_are_explicit_exceptions(name, reason):
    assert scan_control_policy.capability(name) == {
        "supported": False, "unsupported_reason": reason,
        "protected": True, "resume_policy": None,
    }
    assert scan_control._supported(name) is False


@pytest.mark.parametrize("name", [
    None, True, 1, [], {}, b"orb", "", "ORB", " orb", "orb ",
    "orb\n", "strat_bad-name", "strat_bad/name", "strat_\u00e4",
    "crypto_strat_BAD", "crypto_strat_bad.name", "strat_" + "a" * 91,
    "crypto_strat_" + "a" * 85, "unknown", "strategy", "bi_", "crypto_strat",
    "new_listing_extra", "crypto_trade_signals_extra", "penny_positions_extra",
])
def test_invalid_or_unknown_keys_fail_closed_without_identity_normalization(name):
    assert scan_control_policy.capability(name) == {
        "supported": False, "unsupported_reason": "unsupported_scanner",
        "protected": False, "resume_policy": None,
    }
    assert scan_control._supported(name) is False


@pytest.mark.parametrize("prefix,policy", [
    ("strat_", "continue_if_valid"), ("crypto_strat_", "restart_fresh"),
])
def test_dynamic_owner_identity_has_existing_96_character_bound(prefix, policy):
    valid = prefix + "a" * (96 - len(prefix))
    assert len(valid) == 96
    assert scan_control_policy.capability(valid)["resume_policy"] == policy
    assert scan_control_policy.capability(valid + "a")["supported"] is False


def test_capability_returns_independent_metadata_without_mutating_policy():
    first = scan_control_policy.capability("new_listing")
    first.update(supported=True, protected=False, resume_policy="continue_if_valid")
    assert scan_control_policy.capability("new_listing") == {
        "supported": False, "unsupported_reason": "mixed_position_management",
        "protected": True, "resume_policy": None,
    }


def test_controller_support_validation_delegates_to_canonical_policy(monkeypatch):
    seen = []
    def capability(name):
        seen.append(name)
        return {"supported": name == "fixture_owner"}
    monkeypatch.setattr(scan_control_policy, "capability", capability)
    assert scan_control._supported("fixture_owner") is True
    assert scan_control._supported("orb") is False
    assert seen == ["fixture_owner", "orb"]


@pytest.mark.parametrize("name", LEGACY + DEDICATED + CRYPTO_STRATEGIES)
def test_controller_registers_every_supported_owner(monkeypatch, name):
    monkeypatch.setattr(scan_control, "_CONTROLS", {})
    token = ("policy_test",)
    state = scan_control.register(
        name, "policy-run", data_token=token,
        current_data_token=lambda: token, auto_allowed=lambda: True,
    )
    try:
        assert state["owner_scan_key"] == name and state["state"] == "running"
        assert scan_control.snapshot(name)["run_id"] == "policy-run"
    finally:
        scan_control.end(name, "policy-run")


@pytest.mark.parametrize("name", tuple(PROTECTED) + ("unknown", "crypto_strat_BAD"))
def test_controller_never_registers_protected_or_unsupported_owner(monkeypatch, name):
    monkeypatch.setattr(scan_control, "_CONTROLS", {})
    with pytest.raises(ValueError, match="^scan_control_unsupported$"):
        scan_control.register(
            name, "policy-run", data_token=("policy_test",),
            current_data_token=lambda: ("policy_test",), auto_allowed=lambda: True,
        )
    assert scan_control._CONTROLS == {}


def test_bound_owner_is_immutable_and_nested_binding_restores_parent(monkeypatch):
    monkeypatch.setattr(scan_control, "_CONTROLS", {})
    monkeypatch.setattr(scan_control, "_LOCAL", threading.local())
    for name in ("orb", "bear"):
        scan_control.register(
            name, "policy-run", data_token=("policy_test",),
            current_data_token=lambda: ("policy_test",), auto_allowed=lambda: True,
        )
    try:
        assert scan_control.bound_owner() is None
        with scan_control.bind("orb", "policy-run"):
            owner = scan_control.bound_owner()
            assert owner == ("orb", "policy-run") and type(owner) is tuple
            with pytest.raises(TypeError):
                owner[0] = "bear"
            with scan_control.bind("bear", "policy-run"):
                assert scan_control.bound_owner() == ("bear", "policy-run")
            assert scan_control.bound_owner() == owner
        assert scan_control.bound_owner() is None
    finally:
        for name in ("orb", "bear"):
            scan_control.end(name, "policy-run")


@pytest.mark.parametrize("state", sorted(scan_control.STATES))
@pytest.mark.parametrize("ended", [False, True])
def test_pause_pending_requires_live_paused_or_requested_state(monkeypatch, state, ended):
    entry = {"run_id": "policy-run", "ended": ended, "state": state}
    before = dict(entry)
    monkeypatch.setattr(scan_control, "_CONTROLS", {"orb": entry})
    expected = not ended and state in {"pause_requested", "paused"}
    assert scan_control.pause_pending(("orb", "policy-run")) is expected
    assert scan_control.pause_pending(("orb", "policy-run")) is expected
    assert entry == before


@pytest.mark.parametrize("owner", [
    None, "orb", ["orb", "policy-run"], (), ("orb",),
    ("orb", "policy-run", "extra"), (None, "policy-run"),
    ("orb", "stale-run"), ("bear", "policy-run"),
    ("new_listing", "policy-run"), ("ORB", "policy-run"),
])
def test_pause_pending_rejects_missing_stale_or_invalid_owner(monkeypatch, owner):
    entry = {"run_id": "policy-run", "ended": False, "state": "pause_requested"}
    monkeypatch.setattr(scan_control, "_CONTROLS", {"orb": entry})
    assert scan_control.pause_pending(owner) is False


def test_child_can_observe_captured_owner_without_inheriting_binding(monkeypatch):
    monkeypatch.setattr(scan_control, "_CONTROLS", {})
    monkeypatch.setattr(scan_control, "_LOCAL", threading.local())
    scan_control.register(
        "crypto_explosion", "policy-run", data_token=("policy_test",),
        current_data_token=lambda: ("policy_test",), auto_allowed=lambda: True,
    )
    observed = []
    try:
        with scan_control.bind("crypto_explosion", "policy-run"):
            owner = scan_control.bound_owner()
            scan_control.request_pause(*owner, auto_resume=False)
            worker = threading.Thread(target=lambda: observed.append(
                (scan_control.bound_owner(), scan_control.pause_pending(owner))), daemon=True)
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert observed == [(None, True)]
            assert scan_control.snapshot(owner[0])["state"] == "pause_requested"
            scan_control.request_resume(*owner)
            assert scan_control.pause_pending(owner) is False
            scan_control.end(*owner)
            assert scan_control.pause_pending(owner) is False
    finally:
        scan_control.end("crypto_explosion", "policy-run")
