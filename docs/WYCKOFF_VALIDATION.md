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

- `distinct_signal_count` deduplicates stable directional SC/BC, AR, ST,
  SOS/SOW and LPS/LPSY observation/confirmation times. Additional bars, a changing
  entry/score or a phase-E continuation do not count the same chain again.
- Each observation states its cutoff, engine status and active/new identities.
  Insufficient or invalid evidence is not silently treated as “no pattern”.
- Without labels, `label_comparison` is `null`: there is no accuracy claim.
  With labels, only explicitly labelled, analyzable observations enter the
  confusion counts, precision and recall. Unanalyzable labels remain separately
  unscored. Repeated neighbouring cutoffs are correlated, not independent samples.
- There are **no profit, win-rate, return or R calculations**. Entry execution,
  spread, slippage, fees, borrow availability and portfolio overlap are not
  supplied; inventing these would make a misleading trading backtest.

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
