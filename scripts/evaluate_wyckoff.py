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

from modules.wyckoff import MODEL, analyze_wyckoff


MAX_BARS = 2000
DISCLAIMER = (
    "Recognition replay only. Unlabelled observations are not negatives. "
    "Synthetic fixtures and recognition metrics do not prove live accuracy or profitability. "
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
        if cutoff not in cutoffs or direction not in {"LONG", "SHORT"} or type(expected) is not bool:
            raise ValueError("labels require a replayed cutoff, LONG/SHORT and boolean expected_signal")
        key = (cutoff, direction)
        if key in seen:
            raise ValueError("duplicate label cutoff/direction")
        seen.add(key)
        normalized_labels.append({"cutoff": cutoff, "direction": direction, "expected_signal": expected})
    return timeframe, as_of, eligible, sorted(cutoffs), normalized_labels


def _signal_identity(pattern):
    # Neither entry/score nor latest candle belongs to setup identity. A phase-E
    # continuation must not count the same confirmed event chain a second time.
    direction = pattern["direction"]
    names = ("SC", "AR", "ST", "SOS", "LPS") if direction == "LONG" else ("BC", "AR", "ST", "SOW", "LPSY")
    proof = []
    for name in names:
        events = [event for event in pattern.get("events", []) if event.get("name") == name]
        if len(events) != 1:
            raise ValueError(f"trade-ready pattern has no unique {name} evidence")
        event = events[0]
        proof.append({"name": name,
                      "observed_at": _iso(_timestamp(event.get("observed_at"), "event.observed_at")),
                      "confirmed_at": _iso(_timestamp(event.get("confirmed_at"), "event.confirmed_at"))})
    payload = {"model": MODEL, "direction": direction, "events": proof}
    identity = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:24]
    return identity, proof


def evaluate(document):
    """Replay only prefixes available at each explicit completed-bar cutoff."""
    timeframe, as_of, bars, cutoffs, labels = _validate(document)
    signals, observations = {}, []
    for cutoff in cutoffs:
        prefix = [bar for bar in bars if bar["close_time"] <= cutoff]
        result = analyze_wyckoff(prefix, as_of=cutoff, timeframe=timeframe, direction="ALL")
        active, new = {}, []
        for pattern in result.get("patterns", []):
            if result["status"] != "ok" or pattern.get("trade_ready") is not True:
                continue
            identity, proof = _signal_identity(pattern)
            active[pattern["direction"]] = identity
            if identity not in signals:
                signals[identity] = {"id": identity, "direction": pattern["direction"],
                                     "first_seen_at": cutoff, "events": proof}
                new.append(identity)
        observations.append({"cutoff": cutoff, "bars_used": result["bars_used"],
                             "status": result["status"], "reason": result.get("reason"),
                             "active_signals": active, "new_signal_ids": new})
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
                      "labels_supplied": len(labels), "labels_scored": sum(counts.values()),
                      "unscored_labels": unscored, **dict(counts),
                      "precision": tp / (tp + fp) if tp + fp else None,
                      "recall": tp / (tp + fn) if tp + fn else None}
    return {"schema_version": 1, "model": MODEL, "timeframe": timeframe, "as_of": _iso(as_of),
            "disclaimer": DISCLAIMER, "real_world_accuracy_verified": False,
            "profitability_evaluated": False, "completed_bars": len(bars),
            "replayed_cutoffs": len(observations), "distinct_signal_count": len(signals),
            "signals": list(signals.values()), "observations": observations,
            "label_comparison": comparison}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Local JSON OHLCV dataset; no URLs")
    parser.add_argument("--output", type=Path, help="New local report file; existing files are never overwritten")
    args = parser.parse_args(argv)
    try:
        document = json.loads(args.input.read_text(encoding="utf-8-sig"))
        report = evaluate(document)
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
