"""Independent malformed-evidence tests: no scanner/provider/SMTP calls."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from modules.wyckoff_contract import validate_entry_trigger

NOW = datetime(2026, 9, 21, tzinfo=timezone.utc)


def proof(direction="LONG", kind="climactic", phase="D"):
    names = ["SC" if direction == "LONG" else "BC", "AR", "ST",
             "SOS" if direction == "LONG" else "SOW", "LPS" if direction == "LONG" else "LPSY"]
    if kind != "climactic":
        names[0] = "RangeOrigin"
    events = [{"event_id": f"e{i}", "name": name,
               "observed_at": (NOW - timedelta(days=20-i*3)).isoformat(),
               "confirmed_at": (NOW - timedelta(days=19-i*3)).isoformat(),
               "price": 100., "volume_ratio": 1.}
              for i, name in enumerate(names)]
    structure_type = "Accumulation" if direction == "LONG" else "Distribution"
    if kind == "continuation":
        structure_type = "Reaccumulation" if direction == "LONG" else "Redistribution"
    refs = dict(zip(("origin", "reaction", "test", "breakout", "retest"), [e["event_id"] for e in events]))
    return {"model": "causal_wyckoff_v3", "timeframe": "1D", "structure_id": "s1",
            "direction": direction, "structure_type": structure_type, "origin_kind": kind,
            "structure_state": "continuation" if phase == "E" else "confirmed",
            "entry_state": "ready", "trade_ready": True, "signal_state": "confirmed", "phase": phase,
            "events": events, "latest_completed_at": NOW.isoformat(),
            "range_confirmed_at": events[1]["confirmed_at"],
            "signal_confirmed_at": events[4]["confirmed_at"],
            "entry_trigger": {"trigger_id": "t1", "state": "ready", "reason": None,
                              "event_ids": refs, "confirmed_at": events[4]["confirmed_at"]}}


def valid(row):
    return validate_entry_trigger(row, as_of=NOW, model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("kind", ["climactic", "nonclimactic", "continuation"])
@pytest.mark.parametrize("phase", ["D", "E"])
def test_supported_ready_structures_still_require_the_same_five_anchors(direction, kind, phase):
    row = proof(direction, kind, phase)
    assert valid(row) is row["entry_trigger"]


@pytest.mark.parametrize("field,value", [
    ("model", "causal_wyckoff_v2"), ("timeframe", "1H"), ("trade_ready", "true"),
    ("entry_state", "target_passed"), ("entry_state", "stopped"),
    ("structure_state", "failed"), ("structure_state", "unclear"),
    ("phase", "C"), ("signal_state", "context"), ("invalidation_reason", "stop_hit"),
    ("structure_type", "Distribution"), ("origin_kind", "continuation"),
    ("latest_completed_at", "2026-09-21T00:00:00"), ("structure_id", ""),
])
def test_invalid_ready_contract_is_rejected(field, value):
    row = proof()
    row[field] = value
    assert valid(row) is None


def test_repeated_events_do_not_replace_nominated_anchors_or_force_exactly_one():
    row = proof()
    repeated = deepcopy(row["events"][2])
    repeated.update(event_id="st2", observed_at="2026-09-10T00:00:00+00:00",
                    confirmed_at="2026-09-11T00:00:00+00:00")
    row["events"].insert(0, repeated)
    assert valid(row)
    row["entry_trigger"]["event_ids"]["test"] = "missing"
    assert valid(row) is None


@pytest.mark.parametrize("damage", ["duplicate_id", "missing_anchor", "reversed_time", "future_time",
                                     "bad_price", "bad_volume", "foreign_structure", "same_bar"])
def test_adversarial_anchor_payloads_fail_closed(damage):
    row = proof()
    event = row["events"][4]
    if damage == "duplicate_id": row["events"].append(deepcopy(event))
    elif damage == "missing_anchor": row["events"].pop()
    elif damage == "reversed_time": event["observed_at"] = NOW.isoformat()
    elif damage == "future_time": event["confirmed_at"] = (NOW+timedelta(days=1)).isoformat()
    elif damage == "bad_price": event["price"] = float("nan")
    elif damage == "bad_volume": event["volume_ratio"] = True
    elif damage == "foreign_structure": event["structure_id"] = "foreign"
    elif damage == "same_bar": event["observed_at"] = row["events"][3]["confirmed_at"]
    assert valid(row) is None


@pytest.mark.parametrize("value", [[], {}, None, True, 17])
@pytest.mark.parametrize("field", ["entry_state", "signal_state", "structure_state", "phase",
                                    "direction", "structure_type", "origin_kind"])
def test_json_type_confusion_never_raises(field, value):
    row = proof()
    row[field] = value
    assert valid(row) is None


def test_mutated_trigger_state_cannot_be_resurrected_by_ready_parent_flags():
    row = proof()
    row["entry_trigger"]["state"] = "stopped"
    assert valid(row) is None
    row["entry_trigger"]["state"] = "ready"
    row["events"][0]["price"] = 10 ** 1000
    assert valid(row) is None
