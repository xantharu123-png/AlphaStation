# Wyckoff recognition validation, not a profit backtest

`scripts/evaluate_wyckoff.py` replays local OHLCV JSON through the current shared
Wyckoff engine. It has no provider, server, credential, mail or trading interface.
It does not fetch data or change an existing dataset. Keep datasets and reports
under private `output/`, not in Git.

## Input contract

The JSON object requires `timeframe` (for example `1D`), an explicit timezone-aware
`as_of`, and `bars`. Each bar needs numeric `open`, `high`, `low`, `close`, `volume`
and ISO `open_time` **and actual `close_time`**, both with timezones. Obtain daily
close times from the real exchange-session calendar, including early closes; do
not substitute midnight or assume every weekday is a session. The tool does not
infer session clocks or repair prices. Bars must describe completed candles.
Missing/invalid volume, inconsistent OHLC, duplicate closes and malformed clocks
are errors rather than negative recognition results. The dataset limit is 2,000
bars; use bounded windows for a quick local evaluation.
This input bound does not override the engine's stricter 720-bar analysis bound:
longer prefixes remain explicitly `analysis_window_exceeds_bounded_model` and
their labels are unscored, never converted into negative detections.

Default replay cutoffs are every completed bar close at or before `as_of`. An
optional `cutoffs` array can select distinct closes for a bounded experiment.
For each cutoff only the available prefix is passed to the detector: later data
cannot confirm an earlier event. The model version is recorded in the report.

Optional labels use this shape:

```json
{
  "label_source": "Reviewer and frozen dataset/version identifier",
  "labels": [
    {
      "cutoff": "2026-09-18T20:00:00Z",
      "direction": "LONG",
      "expected_signal": true
    }
  ]
}
```

Labels must refer to replayed cutoffs. `expected_signal` asks whether a confirmed
actionable Wyckoff pattern exists **at that cutoff**, not whether a trade will win
and not whether it is the first occurrence. Each cutoff/direction may have one
label. Supply labels independently of engine output, with reviewers seeing only
the same historical prefix. The tool records the source but cannot certify label
independence. **An unlabelled observation is never a negative example.**

## Run locally

From Windows PowerShell in the project directory:

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_wyckoff.py output/wyckoff-input.json --output output/wyckoff-report.json
```

The output must be a new file; existing files, including the input, are never
overwritten. Without `--output`, JSON is printed to the terminal. The supplied
JSON must contain the complete required dataset fields, not only the label
fragment above. No Hetzner update, scan or account login is involved.

## What the report means

- Report schema v2 uses explicit `structure_id`, `event_id` and the selected
  `entry_trigger.trigger_id`. Its five `event_ids` anchors are `origin`,
  `reaction`, `test`, `breakout`, `retest`. Repeated ST/LPS events are permitted:
  the nominated anchors, not the first event with a name, define the signal.
  Additional bars, a changing entry/score or phase E cannot count the same
  trigger again. Reusing a trigger ID for changed evidence is an error.
- `active_signals` maps each direction to a **list** of trigger IDs. Multiple
  structures and later distinct triggers in one direction are retained.
  `distinct_structure_count` is separate from `distinct_signal_count`.
  Each observation preserves structure state, entry state, phase and dated
  event/phase evidence, including non-actionable chart context. The shared API
  trigger guard is also applied here. Future or undated candle evidence fails
  the replay rather than becoming an apparent negative result.
- Each observation states its cutoff, engine status and active/new identities.
  Insufficient or invalid evidence is not silently treated as “no pattern”.
- Without labels, `label_comparison` is `null`: there is no accuracy claim.
  With labels, only explicitly labelled, analyzable observations enter the
  confusion counts, precision and recall. Unanalyzable labels remain separately
  unscored. Repeated neighbouring cutoffs are correlated, not independent samples.
  The report makes this explicit with `correlated_cutoffs: true` and no invented
  independent sample count.
- There are **no profit, win-rate, return or R calculations**. Entry execution,
  spread, slippage, fees, borrow availability and portfolio overlap are not
  supplied; inventing these would make a misleading trading backtest.

## Independent structure and event annotations

The old signal-Boolean labels remain optional and separate. Optional
`structure_labels` and `event_labels` annotate **reviewed scopes**, not model IDs.
Scopes identify a replayed cutoff, direction and one of `Accumulation`,
`Distribution`, `Reaccumulation`, `Redistribution`. Event scopes also specify
`event_name`. For example, this shape describes a synthetic annotation, not a
claim that a real market structure exists:

```json
{
  "label_source": "Independent reviewer and frozen annotation protocol",
  "structure_labels": [{
    "cutoff": "2026-09-18T20:00:00Z",
    "direction": "LONG",
    "structure_type": "Reaccumulation",
    "coverage": "positive_only",
    "expected": [{
      "label_id": "reviewer-structure-17",
      "observed_at": "2026-08-03T20:00:00Z",
      "confirmed_at": "2026-09-17T20:00:00Z",
      "phase": "E",
      "tolerance_seconds": 86400
    }]
  }],
  "event_labels": [{
    "cutoff": "2026-09-18T20:00:00Z",
    "direction": "LONG",
    "structure_type": "Reaccumulation",
    "event_name": "ST",
    "coverage": "complete",
    "expected": []
  }]
}
```

For a structure, `observed_at` is the independently identified SC/BC/RangeOrigin
close. For an event it is that event's observation close. Labels do not reuse
model-generated IDs. `confirmed_at`, if supplied, means the reviewer's earliest
knowable confirmation (of the specified phase, when present), never a hindsight
date. `phase` is optional and only valid for structure labels. Timestamp tolerance
is in elapsed seconds, defaults to zero, and must be frozen before evaluation.
No weekend/session-bar tolerance is silently inferred.

- `coverage: complete` declares all instances in that exact reviewed scope;
  unmatched model instances count as false positives. An empty `expected` list
  is an explicit negative for that scope, not for other directions/types/events.
- `coverage: positive_only` lists known positives only. Unmatched model instances
  are **unscored**, not false positives; no precision can be inferred from them.
- `expected: null` marks ambiguous/unscored scope. Missing scopes and labels are
  also unscored. Unanalyzable engine cutoffs cannot become false negatives.
- Matching is deterministic maximum-cardinality, one-to-one within the supplied
  observation-time tolerance. Matching does not require a correct phase: phase
  agreement is reported separately so wrong E attribution stays visible.
- `structure_comparison`/`event_comparison` report matched and missed references,
  false positives only in complete scopes, recall over annotated references,
  precision only over complete scopes, per-type/direction counts and phase
  agreement. Match details expose confirmation latency in seconds; negative
  values mean the model confirmed earlier than the independent reference.
  Missing reference confirmation yields null latency, not zero.
  `first_replayed_detection_latency_seconds` separately includes delay caused by
  the selected replay cutoffs; sparse replay is not the engine's continuous
  detection latency. Both metrics require explicit reference confirmation times.

Labels are limited to 100 references per scope and 2,000 structural/event
references in total. A label repeated at neighbouring cutoffs remains correlated;
`unique_label_ids` is descriptive and is not an independent sample estimate.
The tool records the annotation source but cannot verify independence or quality.

## Frozen provenance and a later paired cost study

Every evaluation report includes `frozen_evidence.bars_sha256` and `labels_sha256`. These hash
normalized completed OHLCV at/before `as_of` and normalized signal/structure/event
annotations plus `label_source`, respectively (canonical sorted-key JSON, compact separators, UTF-8).
Changing input order or equivalent timezone notation does not alter OHLCV hashes.
Keep the original files, manifest, model commit and evaluation protocol together.
`evaluation_sha256` additionally binds normalized `cutoffs`, `timeframe`, `as_of`,
report schema, model and the full actual parameter dictionary/version. Dropping
an early cutoff changes first-detection latency and therefore invalidates this
hash even when the OHLCV and labels themselves are unchanged.

An optional `frozen_dataset` object binds those exact hashes and requires nonempty
`dataset_id`, `source`, `symbol`, `market`, `adjustment_policy`, `session_calendar`,
`selection_policy`, `parameters_version`, `evaluation_protocol`, plus
`input_kind` (`historical_export` or `synthetic_fixture`) and a chronological
`split` with timezone-aware `calibration_end` strictly before `holdout_start`.
All four hashes are required: `bars_sha256`, `labels_sha256`,
`evaluation_sha256`, and `manifest_sha256`. The last one binds the supported
manifest metadata listed above, including normalized `split`, `selection_policy`
and `evaluation_protocol`; it excludes the hashes themselves. Changing a
selection/protocol/split is not allowed under an old freeze. Equivalent timezone
notation or reordering the same cutoff set does not change normalized hashes.
A mismatch fails evaluation. `parameters_version` must match the actual engine
parameter version (currently `wyckoff_v3_rules_1`), also recorded in each report.
Hash agreement does **not** certify provider data,
calendar correctness, prior freezing or independent labels:
`provenance_verified` and `real_world_accuracy_verified` remain false.

The explicit two-step freeze workflow does not require inspecting holdout results:

1. Prepare the independently authored dataset, labels, complete manifest metadata
   and holdout-only cutoffs, omitting the four hashes from `frozen_dataset`.
   Run the command below. It validates the inputs and creates a **candidate**
   manifest without running the engine, producing metrics or certifying a freeze.
2. Review and preserve that output, then copy its `frozen_dataset` object into the
   unchanged input document. Run normal evaluation with a new report path.
   Archive the exact input/manifest and code commit before examining results.

```powershell
.\.venv\Scripts\python.exe scripts/evaluate_wyckoff.py output/wyckoff-draft.json --prepare-freeze --output output/wyckoff-manifest-candidate.json
.\.venv\Scripts\python.exe scripts/evaluate_wyckoff.py output/wyckoff-frozen-input.json --output output/wyckoff-report.json
```

Preparation reports `evaluation_performed: false` and `hash_binding_verified:
false`; normal evaluation verifies the supplied four hashes rather than silently
refreshing them. Deliberately regenerating a manifest creates a new study version,
not proof that its configuration was frozen before seeing results. These hashes
bind supplied source/configuration contents, not independent authorship or source
truth. Existing output files are never overwritten in either mode.

With a frozen manifest, all replay `cutoffs` must be at or after `holdout_start`;
mixed calibration/holdout or pre-holdout evaluation is rejected. Earlier bars
can and should remain as causal warmup context. Set explicit holdout-only
`cutoffs` when the dataset includes warmup history. Never tune
thresholds after viewing holdout metrics. Freeze both v2 and v3 implementations
and use the same dataset/selection; this tool does not emulate old v2 behavior.

With a frozen manifest the report also emits `cohort_selection_snapshots` for
**both directions at every supplied cutoff**, including nonselections. The shared
input snapshot and `input_sha256` are compatible with
`modules.scanner_cohort_comparison.input_fingerprint`. Opportunity IDs derive from
dataset/cutoff/direction, not model trigger IDs, so later old/new assembly can
retain added/dropped opportunities as well as their intersection. These are
correlated cutoff opportunities, **not one independent trade each**.

Selected opportunities have `state: MISSING`: there is no inferred fill, return
or zero cost. A known nonselection has `NOT_SELECTED`. An unanalyzable cutoff has
`selected: null`, `state: MISSING`; the existing cohort comparer rejects this
until the analysis gap is resolved, rather than treating it as a negative.
A separate, frozen execution protocol must identify actual distinct trade
opportunities and attach chronological outcomes, fills and costs before invoking
the existing cohort comparer. No second profitability engine is implemented here.

## Before claiming reliability

Freeze model thresholds, data provenance/adjustment policy and the evaluation
protocol before examining the holdout period. Use independently reviewed positive,
negative and ambiguous examples across different market regimes, including
accumulation without Spring, distribution without UTAD, failed ranges and ordinary
trend pullbacks. Preserve ambiguous/unlabelled cases rather than calling them
negatives. Split chronologically, avoid overlapping-pattern leakage between
calibration and holdout, and report sample counts by timeframe/direction. Also
verify event drawings against the same dated candles in the chart.

Synthetic regression tests establish software contracts, not recognition quality
on real markets. A later, separately specified execution-and-cost study is required
for profitability; successful recognition alone is not evidence of an edge.
