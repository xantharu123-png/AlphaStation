#!/usr/bin/env python3
"""Local-only, causal Wyckoff recognition replay; not a trading backtest."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.wyckoff import MODEL, PARAMETERS, analyze_wyckoff


MAX_BARS = 2000
STRUCTURE_TYPES = {"Accumulation", "Distribution", "Reaccumulation", "Redistribution"}
TRIGGER_ROLES = ("origin", "reaction", "test", "breakout", "retest")
DISCLAIMER = (
    "Recognition replay only. Unlabelled observations are not negatives. "
    "Synthetic fixtures and recognition metrics do not prove live accuracy or profitability. "
    "Neighbouring cutoffs of the same structure are correlated, not independent samples. "
    "Positive-only annotations cannot establish false positives or precision. "
    "No execution, fill, slippage, fees or profit calculations are performed."
)


def _timestamp(value, name):
    if not isinstance(value, str):
        raise ValueError(f"{name} must be an ISO timestamp with timezone")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be an ISO timestamp with timezone") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} requires an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value):
    # Fixed precision makes chronological prefix comparisons safe even when an
    # input mixes exact seconds with fractional-second provider timestamps.
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _validate(document):
    if not isinstance(document, dict):
        raise ValueError("input must be a JSON object")
    timeframe = document.get("timeframe")
    if not isinstance(timeframe, str) or not re.fullmatch(r"[1-9][0-9]*[MHDW]", timeframe):
        raise ValueError("timeframe must be explicit, for example 1D or 4H")
    as_of = _timestamp(document.get("as_of"), "as_of")
    raw_bars = document.get("bars")
    if not isinstance(raw_bars, list) or not 1 <= len(raw_bars) <= MAX_BARS:
        raise ValueError(f"bars must contain 1..{MAX_BARS} explicit OHLCV candles")
    bars = []
    for index, raw in enumerate(raw_bars):
        if not isinstance(raw, dict):
            raise ValueError(f"bars[{index}] must be an object")
        opened = _timestamp(raw.get("open_time"), f"bars[{index}].open_time")
        closed = _timestamp(raw.get("close_time"), f"bars[{index}].close_time")
        if opened >= closed:
            raise ValueError(f"bars[{index}] must close after opening")
        # This file is completed historical evidence, not a live quote stream.
        for flag in ("is_closed", "complete", "completed", "final"):
            if flag in raw and raw[flag] is not True:
                raise ValueError(f"bars[{index}] must be explicitly completed")
        values = {}
        for key in ("open", "high", "low", "close", "volume"):
            value = raw.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"bars[{index}].{key} must be a finite number")
            values[key] = float(value)
        if min(values[key] for key in ("open", "high", "low", "close")) <= 0 or values["volume"] < 0:
            raise ValueError(f"bars[{index}] has invalid price or volume")
        if values["high"] < max(values["open"], values["close"], values["low"]) or values["low"] > min(values["open"], values["close"]):
            raise ValueError(f"bars[{index}] has inconsistent OHLC prices")
        bars.append({"open_time": _iso(opened), "close_time": _iso(closed), **values})
    bars.sort(key=lambda bar: bar["close_time"])
    close_times = [bar["close_time"] for bar in bars]
    if len(set(close_times)) != len(close_times):
        raise ValueError("duplicate close_time is not allowed")
    eligible = [bar for bar in bars if _timestamp(bar["close_time"], "close_time") <= as_of]
    if not eligible:
        raise ValueError("no completed candles at or before as_of")
    eligible_closes = {bar["close_time"] for bar in eligible}
    raw_cutoffs = document.get("cutoffs", [bar["close_time"] for bar in eligible])
    if not isinstance(raw_cutoffs, list) or not raw_cutoffs:
        raise ValueError("cutoffs must be a nonempty list")
    cutoffs = [_iso(_timestamp(value, "cutoff")) for value in raw_cutoffs]
    if len(set(cutoffs)) != len(cutoffs) or not set(cutoffs).issubset(eligible_closes):
        raise ValueError("cutoffs must be distinct completed close times at or before as_of")
    labels = document.get("labels", [])
    if not isinstance(labels, list):
        raise ValueError("labels must be a list")
    source = document.get("label_source")
    if labels and (not isinstance(source, str) or not source.strip()):
        raise ValueError("label_source is required for independently supplied labels")
    normalized_labels, seen = [], set()
    for label in labels:
        if not isinstance(label, dict):
            raise ValueError("each label must be an object")
        cutoff = _iso(_timestamp(label.get("cutoff"), "label.cutoff"))
        direction = label.get("direction")
        expected = label.get("expected_signal")
        if cutoff not in cutoffs or not isinstance(direction, str) or direction not in {"LONG", "SHORT"} or type(expected) is not bool:
            raise ValueError("labels require a replayed cutoff, LONG/SHORT and boolean expected_signal")
        key = (cutoff, direction)
        if key in seen:
            raise ValueError("duplicate label cutoff/direction")
        seen.add(key)
        normalized_labels.append({"cutoff": cutoff, "direction": direction, "expected_signal": expected})
    return timeframe, as_of, eligible, sorted(cutoffs), normalized_labels


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def _signal_identity(pattern):
    """Resolve the selected trigger, never the first/only event with a name."""
    trigger = pattern.get("entry_trigger")
    if not isinstance(trigger, dict) or not isinstance(trigger.get("trigger_id"), str) or not trigger["trigger_id"]:
        raise ValueError("trade-ready pattern requires an explicit trigger_id")
    if not isinstance(pattern.get("structure_id"), str) or not pattern["structure_id"]:
        raise ValueError("trade-ready pattern requires an explicit structure_id")
    anchors = trigger.get("event_ids")
    if not isinstance(anchors, dict) or set(anchors) != set(TRIGGER_ROLES):
        raise ValueError("trade-ready pattern requires explicit trigger anchors")
    events = {}
    for event in pattern.get("events", []):
        identity = event.get("event_id")
        if identity is None:
            continue  # Non-trigger chart annotations cannot change identity.
        if not isinstance(identity, str) or not identity or identity in events:
            raise ValueError("event_id must be nonempty and unique within a structure")
        events[identity] = event
    proof = []
    for role in TRIGGER_ROLES:
        identity = anchors[role]
        if not isinstance(identity, str) or identity not in events:
            raise ValueError(f"trigger has missing {role} event_id evidence")
        event = events[identity]
        proof.append({"role": role, "event_id": identity, "name": event["name"],
                      "observed_at": _iso(_timestamp(event.get("observed_at"), "event.observed_at")),
                      "confirmed_at": _iso(_timestamp(event.get("confirmed_at"), "event.confirmed_at"))})
    return trigger["trigger_id"], proof


def _validate_annotations(document, cutoffs):
    """Explicit reviewed scopes; absent labels never imply a negative."""
    normalized = {}
    total = 0
    for kind in ("structure", "event"):
        groups = document.get(f"{kind}_labels", [])
        if not isinstance(groups, list) or len(groups) > MAX_BARS:
            raise ValueError(f"{kind}_labels must be a bounded list")
        normalized[kind], scopes = [], set()
        for group in groups:
            if not isinstance(group, dict):
                raise ValueError("annotation scope must be an object")
            cutoff = _iso(_timestamp(group.get("cutoff"), "annotation.cutoff"))
            direction, structure_type = group.get("direction"), group.get("structure_type")
            coverage = group.get("coverage")
            if (cutoff not in cutoffs or not isinstance(direction, str) or direction not in {"LONG", "SHORT"}
                    or not isinstance(structure_type, str) or structure_type not in STRUCTURE_TYPES):
                raise ValueError("annotations need a replayed cutoff, direction and explicit structure_type")
            if not isinstance(coverage, str) or coverage not in {"complete", "positive_only"}:
                raise ValueError("annotation coverage must be complete or positive_only")
            event_name = group.get("event_name") if kind == "event" else None
            if kind == "event" and (not isinstance(event_name, str) or not event_name.strip()):
                raise ValueError("event annotation requires event_name")
            scope = (cutoff, direction, structure_type, event_name)
            if scope in scopes:
                raise ValueError("duplicate annotation scope")
            scopes.add(scope)
            expected = group.get("expected")
            if expected is not None and (not isinstance(expected, list) or len(expected) > 100):
                raise ValueError("expected must be null (ambiguous) or at most 100 explicit annotations")
            items, identities = [], set()
            for item in expected or []:
                if not isinstance(item, dict):
                    raise ValueError("annotation must be an object")
                label_id = item.get("label_id")
                if not isinstance(label_id, str) or not label_id.strip() or label_id in identities:
                    raise ValueError("annotations require unique nonempty label_id within their scope")
                identities.add(label_id)
                observed = _iso(_timestamp(item.get("observed_at"), "annotation.observed_at"))
                confirmed = item.get("confirmed_at")
                if confirmed is not None:
                    confirmed = _iso(_timestamp(confirmed, "annotation.confirmed_at"))
                tolerance = item.get("tolerance_seconds", 0)
                if (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                        or not math.isfinite(tolerance) or tolerance < 0):
                    raise ValueError("annotation tolerance_seconds must be finite and nonnegative")
                if observed > cutoff or (confirmed is not None and not observed <= confirmed <= cutoff):
                    raise ValueError("annotations must be observable and confirmed by their cutoff")
                phase = item.get("phase")
                if phase is not None and (kind != "structure" or not isinstance(phase, str) or phase not in {"A", "B", "C", "D", "E"}):
                    raise ValueError("structure annotation phase must be A..E")
                items.append({"label_id": label_id, "observed_at": observed, "confirmed_at": confirmed,
                              "tolerance_seconds": float(tolerance), "phase": phase})
            total += len(items)
            normalized[kind].append({"cutoff": cutoff, "direction": direction,
                "structure_type": structure_type, "event_name": event_name,
                "coverage": coverage, "expected": items if expected is not None else None})
    if total > MAX_BARS:
        raise ValueError(f"at most {MAX_BARS} structural/event annotations may be replayed")
    if any(normalized.values()) and (not isinstance(document.get("label_source"), str) or not document["label_source"].strip()):
        raise ValueError("label_source is required for structural/event annotations")
    return normalized


def _frozen_evidence(document, bars, labels, annotations, cutoffs, *, verify_hashes=True):
    fingerprints = {"bars_sha256": _digest(bars),
                    "labels_sha256": _digest({"label_source": document.get("label_source"), "signal": labels, **annotations}),
                    "evaluation_sha256": _digest({"schema_version": 2, "model": MODEL,
                        "parameters": dict(PARAMETERS), "timeframe": document["timeframe"],
                        "as_of": _iso(_timestamp(document["as_of"], "as_of")), "cutoffs": cutoffs})}
    manifest = document.get("frozen_dataset")
    if manifest is None:
        return {**fingerprints, "manifest": None, "provenance_verified": False}
    if not isinstance(manifest, dict):
        raise ValueError("frozen_dataset must be an explicit provenance manifest")
    required = ("dataset_id", "source", "symbol", "market", "adjustment_policy", "session_calendar",
                "selection_policy", "parameters_version", "evaluation_protocol")
    for field in required:
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            raise ValueError(f"frozen_dataset requires {field}")
    if not isinstance(manifest.get("input_kind"), str) or manifest["input_kind"] not in {"historical_export", "synthetic_fixture"}:
        raise ValueError("frozen_dataset input_kind must identify historical_export or synthetic_fixture")
    if manifest["parameters_version"] != PARAMETERS["version"]:
        raise ValueError("frozen_dataset parameters_version must match the actual engine parameter version")
    split = manifest.get("split")
    if not isinstance(split, dict):
        raise ValueError("frozen_dataset requires a chronological split")
    calibration_end = _timestamp(split.get("calibration_end"), "split.calibration_end")
    holdout_start = _timestamp(split.get("holdout_start"), "split.holdout_start")
    if calibration_end >= holdout_start:
        raise ValueError("calibration must end before holdout starts")
    if any(_timestamp(cutoff, "cutoff") < holdout_start for cutoff in cutoffs):
        raise ValueError("frozen evaluation cutoffs must be holdout-only; earlier warmup bars may remain")
    normalized_manifest = {field: manifest[field] for field in (*required, "input_kind")}
    normalized_split = {"calibration_end": _iso(calibration_end), "holdout_start": _iso(holdout_start)}
    fingerprints["manifest_sha256"] = _digest({**normalized_manifest, "split": normalized_split})
    if verify_hashes:
        for field, fingerprint in fingerprints.items():
            if manifest.get(field) != fingerprint:
                raise ValueError(f"frozen_dataset {field} does not match normalized replay evidence/configuration")
    return {**fingerprints, "manifest": normalized_manifest, "split": normalized_split,
            "hash_binding_verified": verify_hashes, "provenance_verified": False,
            "meaning": "Hashes bind supplied data, labels, replay schedule and manifest configuration; provenance, prior freezing and independence are not certified."}


def prepare_freeze(document):
    """Prepare a candidate manifest without replaying or claiming prior freezing."""
    _, _, bars, cutoffs, labels = _validate(document)
    annotations = _validate_annotations(document, cutoffs)
    if not isinstance(document.get("frozen_dataset"), dict):
        raise ValueError("freeze preparation requires complete frozen_dataset metadata; hashes may be absent")
    evidence = _frozen_evidence(document, bars, labels, annotations, cutoffs, verify_hashes=False)
    return {"mode": "freeze_preparation_only", "evaluation_performed": False,
            "hash_binding_verified": False, "provenance_verified": False,
            "meaning": "Candidate hashes only. Preserve/review this manifest before a separate evaluation; no model results were calculated.",
            "frozen_dataset": {**evidence["manifest"], "split": evidence["split"],
                **{key: evidence[key] for key in ("bars_sha256", "labels_sha256", "evaluation_sha256", "manifest_sha256")}}}


def _context_evidence(pattern, cutoff, close_times):
    """Preserve separate structure/entry states and dated evidence for label QA."""
    identity = pattern.get("structure_id")
    if not isinstance(identity, str) or not identity:
        raise ValueError("v3 structure_id is required for replay context")
    events, event_ids = [], set()
    for event in pattern.get("events", []):
        if not isinstance(event.get("event_id"), str) or not event["event_id"] or event["event_id"] in event_ids:
            raise ValueError("unique v3 event_id is required for replay evidence")
        event_ids.add(event["event_id"])
        observed = _iso(_timestamp(event.get("observed_at"), "event.observed_at"))
        confirmed = _iso(_timestamp(event.get("confirmed_at"), "event.confirmed_at"))
        if not observed <= confirmed <= cutoff or observed not in close_times or confirmed not in close_times:
            raise ValueError("engine returned noncausal confirmed event evidence")
        events.append({"event_id": event["event_id"], "name": event["name"],
                       "observed_at": observed, "confirmed_at": confirmed})
    origin = next((event for event in events if event["name"] in {"SC", "BC", "RangeOrigin"}), None)
    phases = []
    for phase in pattern.get("phase_evidence", []):
        confirmed = _iso(_timestamp(phase.get("confirmed_at"), "phase.confirmed_at"))
        if confirmed > cutoff or confirmed not in close_times:
            raise ValueError("engine returned noncausal phase evidence")
        phases.append({"phase": phase["phase"], "confirmed_at": confirmed, "status": phase.get("status")})
    return {"structure_id": identity, "direction": pattern["direction"],
            "structure_type": pattern.get("structure_type"), "structure_state": pattern.get("structure_state"),
            "entry_state": pattern.get("entry_state"), "phase": pattern.get("phase"),
            "observed_at": origin["observed_at"] if origin else None,
            "confirmed_at": phases[0]["confirmed_at"] if phases else None,
            "phase_evidence": phases, "events": events}


def _match_annotations(expected, predicted):
    """Deterministic maximum-cardinality matching within predeclared time tolerance."""
    candidates = []
    for label in expected:
        instant = _timestamp(label["observed_at"], "annotation.observed_at")
        distances = [(abs((_timestamp(row["observed_at"], "prediction.observed_at") - instant).total_seconds()), i)
                     for i, row in enumerate(predicted) if row.get("observed_at")]
        candidates.append([i for distance, i in sorted(distances) if distance <= label["tolerance_seconds"]])
    owners = {}

    def assign(label_index, visited):
        for candidate in candidates[label_index]:
            if candidate in visited:
                continue
            visited.add(candidate)
            if candidate not in owners or assign(owners[candidate], visited):
                owners[candidate] = label_index
                return True
        return False

    for label_index in range(len(expected)):
        assign(label_index, set())
    return sorted((label_index, candidate) for candidate, label_index in owners.items())


def _annotation_comparison(groups, observations, kind, label_source):
    if not groups:
        return None
    by_cutoff = {row["cutoff"]: row for row in observations}
    details, totals = [], {}
    for group in groups:
        observation = by_cutoff[group["cutoff"]]
        key = (group["direction"], group["structure_type"], group["event_name"])
        total = totals.setdefault(key, {"direction": key[0], "structure_type": key[1], "event_name": key[2],
            "matched": 0, "missed": 0, "false_positive": 0, "complete_scope_matches": 0,
            "complete_scopes": 0, "positive_only_unscored_predictions": 0, "phase_agreed": 0, "phase_scored": 0})
        if observation["status"] != "ok" or group["expected"] is None:
            details.append({**group, "scored": False,
                            "reason": "ambiguous_label" if group["expected"] is None else observation["reason"] or observation["status"]})
            continue
        contexts = [row for row in observation["structures"]
                    if row["direction"] == group["direction"] and row["structure_type"] == group["structure_type"]]
        predicted = contexts if kind == "structure" else [
            {**event, "structure_id": row["structure_id"]} for row in contexts for event in row["events"]
            if event["name"] == group["event_name"]]
        matches = _match_annotations(group["expected"], predicted)
        matched = []
        for label_index, prediction_index in matches:
            label, prediction = group["expected"][label_index], predicted[prediction_index]
            confirmation = prediction.get("confirmed_at")
            first_seen = prediction.get("first_seen_at")
            phase_agrees = None
            if label["phase"]:
                phase_agrees = prediction["phase"] == label["phase"]
                total["phase_scored"] += 1
                total["phase_agreed"] += int(phase_agrees)
                confirmation = next((phase["confirmed_at"] for phase in prediction["phase_evidence"]
                                     if phase["phase"] == label["phase"]), None)
                first_seen = next((phase["first_seen_at"] for phase in prediction["phase_evidence"]
                                   if phase["phase"] == label["phase"]), None)
            latency = ((_timestamp(confirmation, "prediction.confirmed_at") - _timestamp(label["confirmed_at"], "label.confirmed_at")).total_seconds()
                       if confirmation is not None and label["confirmed_at"] is not None else None)
            matched.append({"label_id": label["label_id"],
                "prediction_id": prediction.get("event_id", prediction["structure_id"]),
                "structure_id": prediction["structure_id"], "phase_agrees": phase_agrees,
                "confirmation_latency_seconds": latency,
                "first_replayed_detection_latency_seconds": (
                    (_timestamp(first_seen, "prediction.first_seen_at") - _timestamp(label["confirmed_at"], "label.confirmed_at")).total_seconds()
                    if first_seen is not None and label["confirmed_at"] is not None else None)})
        unmatched = len(predicted) - len(matches)
        complete = group["coverage"] == "complete"
        total["matched"] += len(matches)
        total["missed"] += len(group["expected"]) - len(matches)
        total["false_positive"] += unmatched if complete else 0
        total["complete_scope_matches"] += len(matches) if complete else 0
        total["complete_scopes"] += int(complete)
        total["positive_only_unscored_predictions"] += 0 if complete else unmatched
        details.append({"cutoff": group["cutoff"], "direction": group["direction"],
            "structure_type": group["structure_type"], "event_name": group["event_name"],
            "coverage": group["coverage"], "scored": True, "matches": matched,
            "missed_label_ids": [item["label_id"] for i, item in enumerate(group["expected"]) if i not in {a for a, _ in matches}],
            "false_positive": unmatched if complete else None,
            "unscored_predictions": 0 if complete else unmatched})
    for total in totals.values():
        complete_predictions = total["complete_scope_matches"] + total["false_positive"]
        total["precision"] = total["complete_scope_matches"] / complete_predictions if complete_predictions else None
        total["recall"] = total["matched"] / (total["matched"] + total["missed"]) if total["matched"] + total["missed"] else None
        total["phase_agreement"] = total["phase_agreed"] / total["phase_scored"] if total["phase_scored"] else None
    return {"label_source": label_source, "independence_verified": False,
            "matching": "one_to_one_maximum_cardinality_observed_at_with_supplied_tolerance",
            "precision_scope": "complete annotations only; positive-only predictions are not false positives",
            "correlated_cutoffs": True, "independent_sample_count": None,
            "unique_label_ids": len({item["label_id"] for group in groups for item in group["expected"] or []}),
            "by_type_direction": list(totals.values()), "scopes": details}


def evaluate(document):
    """Replay only prefixes available at each explicit completed-bar cutoff."""
    from modules.wyckoff_contract import validate_entry_trigger

    timeframe, as_of, bars, cutoffs, labels = _validate(document)
    annotations = _validate_annotations(document, cutoffs)
    frozen = _frozen_evidence(document, bars, labels, annotations, cutoffs)
    signals, observations = {}, []
    distinct_structures, first_seen_evidence = set(), {}
    for cutoff in cutoffs:
        prefix = [bar for bar in bars if bar["close_time"] <= cutoff]
        result = analyze_wyckoff(prefix, as_of=cutoff, timeframe=timeframe, direction="ALL")
        active, new, contexts = {}, [], {}
        for pattern in result.get("patterns", []):
            if result["status"] != "ok":
                continue
            context = _context_evidence(pattern, cutoff, {bar["close_time"] for bar in prefix})
            context["first_seen_at"] = first_seen_evidence.setdefault(("structure", context["structure_id"]), cutoff)
            for item in context["phase_evidence"]:
                item["first_seen_at"] = first_seen_evidence.setdefault(("phase", context["structure_id"], item["phase"]), cutoff)
            for item in context["events"]:
                item["first_seen_at"] = first_seen_evidence.setdefault(("event", item["event_id"]), cutoff)
            contexts[context["structure_id"]] = context
            distinct_structures.add(context["structure_id"])
            if pattern.get("trade_ready") is not True:
                continue
            validated = validate_entry_trigger(pattern, as_of=cutoff, timeframe=timeframe, model=MODEL)
            if validated is None:
                raise ValueError("trade-ready pattern failed the shared v3 trigger contract")
            identity, proof = _signal_identity(pattern)
            directional = active.setdefault(pattern["direction"], [])
            if identity not in directional:
                directional.append(identity)
            if identity not in signals:
                signals[identity] = {"id": identity, "direction": pattern["direction"],
                                     "structure_id": pattern["structure_id"],
                                     "structure_type": pattern["structure_type"],
                                     "confirmed_at": _iso(_timestamp(pattern["entry_trigger"]["confirmed_at"], "trigger.confirmed_at")),
                                     "first_seen_at": cutoff, "events": proof}
                new.append(identity)
            elif signals[identity]["events"] != proof or signals[identity]["structure_id"] != pattern["structure_id"]:
                raise ValueError("stable trigger_id was reused for different evidence")
        observations.append({"cutoff": cutoff, "bars_used": result["bars_used"],
                             "status": result["status"], "reason": result.get("reason"),
                             "active_signals": active, "new_signal_ids": new,
                             "structures": list(contexts.values())})
    by_cutoff = {row["cutoff"]: row for row in observations}
    counts = Counter({"true_positive": 0, "false_positive": 0, "true_negative": 0, "false_negative": 0})
    unscored = []
    for label in labels:
        observation = by_cutoff[label["cutoff"]]
        if observation["status"] != "ok":
            unscored.append({**label, "reason": observation["reason"] or observation["status"]})
            continue
        predicted = label["direction"] in observation["active_signals"]
        expected = label["expected_signal"]
        name = ("true_" if predicted == expected else "false_") + ("positive" if predicted else "negative")
        counts[name] += 1
    comparison = None
    if labels:
        tp, fp, tn, fn = (counts[name] for name in ("true_positive", "false_positive", "true_negative", "false_negative"))
        comparison = {"label_source": document["label_source"], "independence_verified": False,
                      "meaning": "active confirmed pattern at labelled cutoff, not new-signal count or trade outcome",
                      "correlated_cutoffs": True, "independent_sample_count": None,
                      "labels_supplied": len(labels), "labels_scored": sum(counts.values()),
                      "unscored_labels": unscored, **dict(counts),
                      "precision": tp / (tp + fp) if tp + fp else None,
                      "recall": tp / (tp + fn) if tp + fn else None}
    selections = None
    if frozen["manifest"] is not None:
        selections = []
        for observation in observations:
            for direction in ("LONG", "SHORT"):
                # Exogenous cutoff opportunities also retain added/dropped
                # selections. Model trigger IDs are not an old/new pairing key.
                snapshot = {"dataset_id": frozen["manifest"]["dataset_id"],
                    "bars_sha256": frozen["bars_sha256"], "symbol": frozen["manifest"]["symbol"],
                    "timeframe": timeframe, "cutoff": observation["cutoff"], "direction": direction}
                selected = bool(observation["active_signals"].get(direction)) if observation["status"] == "ok" else None
                selections.append({"opportunity_id": _digest(snapshot)[:24],
                    "ticker": snapshot["symbol"], "observed_at": observation["cutoff"],
                    "input": snapshot, "input_sha256": _digest(snapshot),
                    "selected": selected, "state": "NOT_SELECTED" if selected is False else "MISSING",
                    "analysis_status": observation["status"],
                    "trigger_ids": observation["active_signals"].get(direction, [])})
    return {"schema_version": 2, "model": MODEL, "parameter_version": PARAMETERS["version"],
            "parameters": dict(PARAMETERS), "timeframe": timeframe, "as_of": _iso(as_of),
            "disclaimer": DISCLAIMER, "real_world_accuracy_verified": False,
            "profitability_evaluated": False, "completed_bars": len(bars),
            "replayed_cutoffs": len(observations), "distinct_signal_count": len(signals),
            "distinct_structure_count": len(distinct_structures), "frozen_evidence": frozen,
            "signals": list(signals.values()), "observations": observations,
            "label_comparison": comparison,
            "structure_comparison": _annotation_comparison(annotations["structure"], observations, "structure", document.get("label_source")),
            "event_comparison": _annotation_comparison(annotations["event"], observations, "event", document.get("label_source")),
            "cohort_selection_snapshots": selections,
            "cohort_snapshot_meaning": "Cutoff selection evidence only, not independent trades; preserve full old/new union and supply actual costs/outcomes separately."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local JSON OHLCV dataset; no URLs")
    parser.add_argument("--output", type=Path, help="New local report file; existing files are never overwritten")
    parser.add_argument("--prepare-freeze", action="store_true",
                        help="Prepare a candidate manifest without running the engine or recognition metrics")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = prepare_freeze(document) if args.prepare_freeze else evaluate(document)
        serialized = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as handle:
                handle.write(serialized)
        else:
            print(serialized, end="")
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        print(f"Wyckoff evaluation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
