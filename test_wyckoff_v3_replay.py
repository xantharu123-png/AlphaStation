"""Replay/schema checks with explicit synthetic evidence, not real-market labels."""
from copy import deepcopy

import pytest

from scripts import evaluate_wyckoff as replay
from test_wyckoff_replay import dataset


def pattern(document, identity="structure-a", trigger="trigger-a", offset=0):
    bars = document["bars"]
    def stamp(index):
        return bars[index + offset]["close_time"]
    roles = dict(zip(replay.TRIGGER_ROLES, ("SC", "AR", "ST", "SOS", "LPS")))
    events = [{"event_id": f"{identity}:{role}", "structure_id": identity,
               "name": name, "observed_at": stamp(index), "confirmed_at": stamp(index + 1),
               "price": 100., "volume_ratio": 1.2}
              for (role, name), index in zip(roles.items(), (25, 35, 45, 65, 75))]
    return {"model": replay.MODEL, "timeframe": "1D", "structure_id": identity,
            "structure_type": "Accumulation", "structure_state": "confirmed", "origin_kind": "climactic",
            "phase": "D", "direction": "LONG", "entry_state": "ready", "signal_state": "confirmed",
            "trade_ready": True, "invalidation_reason": None,
            "latest_completed_at": stamp(80), "range_confirmed_at": stamp(36), "signal_confirmed_at": stamp(76),
            "events": events, "phase_evidence": [{"phase": "D", "confirmed_at": stamp(66), "status": "confirmed"}],
            "entry_trigger": {"trigger_id": trigger, "confirmed_at": stamp(76), "state": "ready", "reason": None,
                "event_ids": {role: event["event_id"] for role, event in zip(roles, events)}}}


def replay_fixture(monkeypatch, rows=None):
    doc = dataset()
    doc["cutoffs"] = [doc["bars"][index]["close_time"] for index in (90, 94, 99)]
    rows = rows or [pattern(doc)]
    monkeypatch.setattr(replay, "analyze_wyckoff", lambda bars, **kwargs:
        {"status": "ok", "bars_used": len(bars), "patterns": deepcopy(rows)})
    return doc, rows


def scope(document, kind="structure", coverage="complete", expected=None):
    result = {"cutoff": document["cutoffs"][-1], "direction": "LONG", "structure_type": "Accumulation",
              "coverage": coverage, "expected": expected if expected is not None else []}
    if kind == "event":
        result["event_name"] = "ST"
    return result


def label(document, index=25, **kwargs):
    return {"label_id": "independent-reference-a", "observed_at": document["bars"][index]["close_time"], **kwargs}


def test_multiple_same_direction_structures_and_triggers_are_not_overwritten(monkeypatch):
    doc = dataset()
    rows = [pattern(doc), pattern(doc, "structure-b", "trigger-b", offset=3)]
    doc, _ = replay_fixture(monkeypatch, rows)
    result = replay.evaluate(doc)
    assert result["schema_version"] == 2
    assert result["distinct_signal_count"] == result["distinct_structure_count"] == 2
    assert all(row["active_signals"]["LONG"] == ["trigger-a", "trigger-b"] for row in result["observations"])
    assert result["observations"][0]["new_signal_ids"] == ["trigger-a", "trigger-b"]
    assert result["observations"][1]["new_signal_ids"] == []


def test_selected_explicit_anchors_allow_repeated_st_lps_and_event_reordering():
    row = pattern(dataset())
    expected = replay._signal_identity(row)
    repeated = deepcopy(row)
    for name in ("ST", "LPS"):
        repeated["events"].append({**row["events"][2 if name == "ST" else 4], "event_id": f"later-{name}"})
    repeated["events"].reverse()
    repeated.update(phase="E", score=99, entry=250, index=500)
    assert replay._signal_identity(repeated) == expected
    repeated["entry_trigger"]["event_ids"]["retest"] = "later-LPS"
    assert replay._signal_identity(repeated) != expected


def test_new_trigger_of_existing_structure_is_counted_but_same_trigger_not_reused(monkeypatch):
    doc, rows = replay_fixture(monkeypatch)
    changed = deepcopy(rows[0])
    changed["entry_trigger"]["trigger_id"] = "trigger-later"
    changed["events"][-1].update(event_id="retest-later", observed_at=doc["bars"][92]["close_time"], confirmed_at=doc["bars"][93]["close_time"])
    changed["entry_trigger"]["event_ids"]["retest"] = "retest-later"
    changed["entry_trigger"]["confirmed_at"] = changed["signal_confirmed_at"] = doc["bars"][93]["close_time"]
    changed["latest_completed_at"] = doc["bars"][94]["close_time"]
    monkeypatch.setattr(replay, "analyze_wyckoff", lambda bars, **kwargs:
        {"status": "ok", "bars_used": len(bars), "patterns": [rows[0] if len(bars) <= 91 else changed]})
    result = replay.evaluate(doc)
    assert result["distinct_signal_count"] == 2 and result["distinct_structure_count"] == 1
    assert result["observations"][-1]["new_signal_ids"] == []
    changed["entry_trigger"]["trigger_id"] = "trigger-a"
    with pytest.raises(ValueError, match="reused"):
        replay.evaluate(doc)


@pytest.mark.parametrize("mutation", [
    lambda row: row["entry_trigger"]["event_ids"].update(test="missing"),
    lambda row: row.update(entry_state="stopped_out"),
    lambda row: row["events"][2].update(volume_ratio=0),
    lambda row: row["events"][2].update(observed_at="2027-01-01T00:00:00Z"),
    lambda row: row["events"][2].update(confirmed_at="2026-02-17T00:00:01Z"),
    lambda row: row["events"].append(deepcopy(row["events"][2])),
])
def test_malformed_ready_evidence_is_error_not_negative(monkeypatch, mutation):
    doc, rows = replay_fixture(monkeypatch)
    mutation(rows[0])
    with pytest.raises(ValueError):
        replay.evaluate(doc)


def test_independent_structure_phase_e_and_event_matching_with_latency(monkeypatch):
    doc, rows = replay_fixture(monkeypatch)
    rows[0].update(phase="E", structure_state="continuation", structure_type="Reaccumulation", origin_kind="continuation")
    rows[0]["events"][0]["name"] = "RangeOrigin"
    rows[0]["phase_evidence"].append({"phase": "E", "confirmed_at": doc["bars"][82]["close_time"], "status": "confirmed"})
    doc["label_source"] = "Independent protocol example; synthetic data"
    doc["structure_labels"] = [scope(doc, expected=[label(doc, phase="E", confirmed_at=doc["bars"][81]["close_time"])] )]
    doc["structure_labels"][0]["structure_type"] = "Reaccumulation"
    doc["event_labels"] = [scope(doc, "event", expected=[label(doc, index=45, confirmed_at=doc["bars"][46]["close_time"])])]
    doc["event_labels"][0]["structure_type"] = "Reaccumulation"
    result = replay.evaluate(doc)
    structure = result["structure_comparison"]
    assert structure["by_type_direction"][0]["precision"] == structure["by_type_direction"][0]["recall"] == 1
    assert structure["by_type_direction"][0]["phase_agreement"] == 1
    assert structure["scopes"][0]["matches"][0]["confirmation_latency_seconds"] == 86400
    assert result["event_comparison"]["scopes"][0]["matches"][0]["confirmation_latency_seconds"] == 0
    assert structure["independence_verified"] is False and structure["independent_sample_count"] is None


def test_positive_only_labels_do_not_impute_false_positives_or_precision(monkeypatch):
    doc = dataset()
    doc, _ = replay_fixture(monkeypatch, [pattern(doc), pattern(doc, "structure-b", "trigger-b", 3)])
    doc["label_source"] = "Synthetic positive-only author"
    doc["structure_labels"] = [scope(doc, coverage="positive_only", expected=[label(doc)])]
    summary = replay.evaluate(doc)["structure_comparison"]["by_type_direction"][0]
    assert summary["recall"] == 1 and summary["precision"] is None
    assert summary["false_positive"] == 0 and summary["positive_only_unscored_predictions"] == 1
    doc["structure_labels"][0]["coverage"] = "complete"
    summary = replay.evaluate(doc)["structure_comparison"]["by_type_direction"][0]
    assert summary["precision"] == .5 and summary["false_positive"] == 1


def test_ambiguous_and_unanalysable_annotations_remain_unscored(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    doc["label_source"] = "Synthetic ambiguous author"
    doc["structure_labels"] = [scope(doc)]
    doc["structure_labels"][0]["expected"] = None
    result = replay.evaluate(doc)["structure_comparison"]
    assert result["scopes"][0]["reason"] == "ambiguous_label"
    assert result["by_type_direction"][0]["precision"] is None
    doc["structure_labels"][0]["expected"] = [label(doc)]
    monkeypatch.setattr(replay, "analyze_wyckoff", lambda bars, **kwargs:
        {"status": "insufficient_data", "reason": "missing_volume", "bars_used": len(bars), "patterns": []})
    result = replay.evaluate(doc)["structure_comparison"]
    assert result["scopes"][0]["reason"] == "missing_volume"
    assert result["by_type_direction"][0]["missed"] == 0


def test_matching_is_one_to_one_not_duplicate_nearest_neighbour():
    observed = "2026-01-01T00:00:00Z"
    expected = [{"observed_at": observed, "tolerance_seconds": 86400},
                {"observed_at": observed, "tolerance_seconds": 0}]
    predicted = [{"observed_at": observed}, {"observed_at": "2026-01-02T00:00:00Z"}]
    assert replay._match_annotations(expected, predicted) == [(0, 1), (1, 0)]
    assert replay._match_annotations(expected, predicted[:1]) == [(0, 0)]


def test_wrong_phase_and_missed_structure_are_visible_in_separate_metrics(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    doc["label_source"] = "Synthetic independent references"
    missed = {**label(doc, index=10), "label_id": "reference-missed"}
    doc["structure_labels"] = [scope(doc, expected=[label(doc, phase="E"), missed])]
    result = replay.evaluate(doc)["structure_comparison"]
    summary = result["by_type_direction"][0]
    assert summary["precision"] == 1 and summary["recall"] == .5
    assert summary["matched"] == 1 and summary["missed"] == 1 and summary["phase_agreement"] == 0
    assert result["scopes"][0]["missed_label_ids"] == ["reference-missed"]
    assert result["scopes"][0]["matches"][0]["confirmation_latency_seconds"] is None


def test_unlabelled_direction_and_event_types_never_become_implicit_negatives(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    doc["label_source"] = "Synthetic explicit negative event scope"
    doc["event_labels"] = [scope(doc, kind="event")]
    result = replay.evaluate(doc)
    assert result["structure_comparison"] is None and result["label_comparison"] is None
    assert len(result["event_comparison"]["by_type_direction"]) == 1
    summary = result["event_comparison"]["by_type_direction"][0]
    assert summary["event_name"] == "ST" and summary["direction"] == "LONG"
    assert summary["false_positive"] == 1 and summary["precision"] == 0 and summary["recall"] is None


def freeze(document, split=None):
    document["frozen_dataset"] = {"dataset_id": "synthetic-frozen-v1", "input_kind": "synthetic_fixture",
        "source": "Test author", "symbol": "SYNTH", "market": "synthetic", "adjustment_policy": "none-synthetic",
        "session_calendar": "synthetic daily bars, not exchange sessions", "selection_policy": "test fixture",
        "parameters_version": replay.PARAMETERS["version"], "evaluation_protocol": "synthetic software regression only",
        "split": split or {"calibration_end": "2026-01-01T00:00:00Z", "holdout_start": "2026-01-02T00:00:00Z"}}
    document["frozen_dataset"] = replay.prepare_freeze(document)["frozen_dataset"]


def test_frozen_provenance_binds_data_and_labels_without_certifying_them(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    result = replay.evaluate(doc)
    assert result["frozen_evidence"]["hash_binding_verified"] is True
    assert result["frozen_evidence"]["provenance_verified"] is False
    snapshots = result["cohort_selection_snapshots"]
    assert len(snapshots) == len(doc["cutoffs"]) * 2
    assert snapshots[0]["state"] == "MISSING" and snapshots[1]["state"] == "NOT_SELECTED"
    from modules.scanner_cohort_comparison import input_fingerprint
    assert snapshots[0]["input_sha256"] == input_fingerprint(snapshots[0]["input"])
    assert not any("gross_r" in row or "roundtrip_cost_r" in row for row in snapshots)
    modified = deepcopy(doc)
    modified["bars"][0]["volume"] += 1
    with pytest.raises(ValueError, match="bars_sha256"):
        replay.evaluate(modified)
    doc["label_source"] = "new labels"
    doc["structure_labels"] = [scope(doc, expected=[label(doc)])]
    with pytest.raises(ValueError, match="labels_sha256"):
        replay.evaluate(doc)


@pytest.mark.parametrize("mutation", [
    lambda d: d.pop("label_source"),
    lambda d: d["structure_labels"][0].update(coverage="inferred"),
    lambda d: d["structure_labels"][0].update(structure_type="arbitrary"),
    lambda d: d["structure_labels"][0].update(structure_type=[]),
    lambda d: d["structure_labels"][0].update(direction={}),
    lambda d: d["structure_labels"][0].update(coverage=[]),
    lambda d: d["structure_labels"][0]["expected"][0].update(tolerance_seconds=-1),
    lambda d: d["structure_labels"][0]["expected"][0].update(tolerance_seconds=True),
    lambda d: d["structure_labels"][0]["expected"][0].update(phase="F"),
    lambda d: d["structure_labels"][0]["expected"][0].update(phase=[]),
    lambda d: d["structure_labels"][0]["expected"][0].update(confirmed_at="2027-01-01T00:00:00Z"),
    lambda d: d["structure_labels"].append(deepcopy(d["structure_labels"][0])),
])
def test_annotation_inputs_fail_closed(monkeypatch, mutation):
    doc, _ = replay_fixture(monkeypatch)
    doc["label_source"] = "Synthetic author"
    doc["structure_labels"] = [scope(doc, expected=[label(doc)])]
    mutation(doc)
    with pytest.raises(ValueError):
        replay.evaluate(doc)


def test_bounded_input_limit_is_retained():
    doc = dataset()
    doc["bars"] = doc["bars"] * 21
    with pytest.raises(ValueError, match="2000"):
        replay.evaluate(doc)


@pytest.mark.parametrize("mutation,message", [
    (lambda d: d["frozen_dataset"]["split"].update(holdout_start="2026-04-06T00:00:00Z"), "holdout-only"),
    (lambda d: d["frozen_dataset"].update(parameters_version="old_parameters"), "parameters_version"),
    (lambda d: d["frozen_dataset"].update(input_kind=[]), "input_kind"),
    (lambda d: d["frozen_dataset"].update(split=[]), "chronological split"),
])
def test_frozen_evaluation_rejects_mixed_partitions_and_wrong_model_parameters(monkeypatch, mutation, message):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    mutation(doc)
    with pytest.raises(ValueError, match=message):
        replay.evaluate(doc)


def test_frozen_holdout_can_keep_earlier_warmup_history(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc, split={"calibration_end": "2026-04-01T00:00:00Z", "holdout_start": doc["cutoffs"][0]})
    result = replay.evaluate(doc)
    assert result["completed_bars"] == 100 and result["replayed_cutoffs"] == 3
    assert result["parameter_version"] == replay.PARAMETERS["version"]


def test_malformed_signal_label_direction_is_value_error(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    doc["label_source"] = "Synthetic malformed label"
    doc["labels"] = [{"cutoff": doc["cutoffs"][0], "direction": [], "expected_signal": True}]
    with pytest.raises(ValueError):
        replay.evaluate(doc)


@pytest.mark.parametrize("mutation,field", [
    (lambda d: d.update(cutoffs=d["cutoffs"][-1:]), "evaluation_sha256"),
    (lambda d: d.update(timeframe="1H"), "evaluation_sha256"),
    (lambda d: d.update(as_of="2026-04-12T00:00:00Z"), "evaluation_sha256"),
    (lambda d: d["frozen_dataset"].update(selection_policy="changed selection"), "manifest_sha256"),
    (lambda d: d["frozen_dataset"].update(evaluation_protocol="changed protocol"), "manifest_sha256"),
    (lambda d: d["frozen_dataset"]["split"].update(holdout_start="2026-02-01T00:00:00Z"), "manifest_sha256"),
    (lambda d: d["frozen_dataset"].pop("evaluation_sha256"), "evaluation_sha256"),
    (lambda d: d["frozen_dataset"].pop("manifest_sha256"), "manifest_sha256"),
])
def test_frozen_manifest_binds_cutoffs_and_evaluation_configuration(monkeypatch, mutation, field):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    mutation(doc)
    with pytest.raises(ValueError, match=field):
        replay.evaluate(doc)


def test_actual_parameter_change_invalidates_evaluation_hash_even_with_same_version(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    monkeypatch.setitem(replay.PARAMETERS, "minimum_swing_atr", .75)
    with pytest.raises(ValueError, match="evaluation_sha256"):
        replay.evaluate(doc)


def test_freeze_hashes_normalize_cutoff_order_and_equivalent_timezones(monkeypatch):
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    doc["cutoffs"].reverse()
    doc["as_of"] = "2026-04-11T02:00:00+02:00"
    doc["frozen_dataset"]["split"]["holdout_start"] = "2026-01-02T02:00:00+02:00"
    assert replay.evaluate(doc)["frozen_evidence"]["hash_binding_verified"] is True


def test_preparation_runs_no_engine_and_does_not_claim_frozen_evaluation(monkeypatch, tmp_path):
    import json
    doc, _ = replay_fixture(monkeypatch)
    freeze(doc)
    for key in ("bars_sha256", "labels_sha256", "evaluation_sha256", "manifest_sha256"):
        doc["frozen_dataset"].pop(key)
    monkeypatch.setattr(replay, "analyze_wyckoff", lambda *args, **kwargs: pytest.fail("Preparation must not evaluate holdout data"))
    prepared = replay.prepare_freeze(doc)
    assert prepared["evaluation_performed"] is prepared["hash_binding_verified"] is False
    assert set(prepared["frozen_dataset"]) >= {"evaluation_sha256", "manifest_sha256"}
    with pytest.raises(ValueError, match="bars_sha256"):
        replay.evaluate(doc)
    source, output = tmp_path / "draft.json", tmp_path / "prepared.json"
    source.write_text(json.dumps(doc), encoding="utf-8")
    assert replay.main([str(source), "--prepare-freeze", "--output", str(output)]) == 0
    assert json.loads(output.read_text(encoding="utf-8"))["evaluation_performed"] is False
    original = output.read_bytes()
    assert replay.main([str(source), "--prepare-freeze", "--output", str(output)]) == 2
    assert output.read_bytes() == original
