"""Versioned, causal OHLC swing evidence; no intrabar path or order-flow claims."""
from __future__ import annotations

from hashlib import sha256
import math

from modules.level_zones import confirmed_pivot_evidence


PARAMETERS = {
    "version": "wyckoff_v3_rules_2", "entry_policy": "confirmed_breakout_optional_retest",
    "pivot_left": 1, "pivot_right": 1,
    "minimum_swing_atr": .50, "major_swing_atr": 2.0,
    "separate_test_bars": 3, "countermove_range_fraction": .20,
    "prior_trend_bars": 15, "prior_trend_fraction": .03,
    "nonclimactic_range_bars": 12, "maximum_contexts_per_direction": 24,
    "maximum_analysis_bars": 720,
}


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def identity(kind, *parts):
    return kind + "_" + sha256("|".join(str(part) for part in parts).encode()).hexdigest()[:24]


def causal_atr(bars):
    """Trailing simple TR baseline, fixed at each observed bar, never latest ATR."""
    values, result = [], []
    for index, bar in enumerate(bars):
        previous = bars[index - 1].close if index else bar.close
        values.append(max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous)))
        result.append(sum(values[-14:]) / min(14, len(values)))
    return result


def swing_evidence(bars, timeframe):
    """Use canonical strict pivots; resolve plateaus by their first extreme.

    A plateau is confirmed only by a strictly departing completed right bar.
    Same-bar high+low candidates have unknowable intrabar order: neither enters
    the pivot chain. Already confirmed pivots are never replaced retrospectively.
    """
    volatility = causal_atr(bars)
    raw = confirmed_pivot_evidence(bars, timeframe=timeframe, as_of=bars[-1].closed_at,
                                   pivot_left=1, pivot_right=1)
    candidates = {}
    for item in raw:
        index = item.provenance["pivot_index"]
        kind = "HIGH" if item.source_name.endswith("high") else "LOW"
        candidates[(index, kind)] = (index + 1, item.lower)
    # Strict primitive deliberately ignores ties. Extend only flat extrema, not
    # a fabricated ordering of an outside candle's two possible pivots.
    for kind, field, sign in (("HIGH", "high", 1), ("LOW", "low", -1)):
        start = 1
        while start < len(bars) - 1:
            price = getattr(bars[start], field)
            end = start
            while end + 1 < len(bars) and getattr(bars[end + 1], field) == price:
                end += 1
            if (end > start and end + 1 < len(bars)
                    and sign * price > sign * getattr(bars[start - 1], field)
                    and sign * price > sign * getattr(bars[end + 1], field)):
                candidates[(start, kind)] = (end + 1, price)
            start = end + 1
    ambiguous = {index for index, kind in candidates
                 if (index, "LOW" if kind == "HIGH" else "HIGH") in candidates}
    # A strict low and not-yet-ended high plateau (or mirror) already has
    # unknown intrabar order. Do not publish it now only to retract it later.
    for index, kind in candidates:
        current, left, right = bars[index], bars[index - 1], bars[index + 1]
        if (current.high > left.high and current.high >= right.high
                and current.low < left.low and current.low <= right.low):
            ambiguous.add(index)
    pivots = []
    for (index, kind), (confirmation, price) in sorted(candidates.items(), key=lambda pair: (pair[1][0], pair[0][0])):
        if index in ambiguous or bars[index].volume <= 0 or bars[confirmation].volume <= 0:
            continue
        if pivots and index <= pivots[-1]["index"]:
            continue
        item = {"pivot_id": identity("pivot", timeframe, kind, iso(bars[index].closed_at)),
                "index": index, "confirmation_index": confirmation, "kind": kind,
                "price": price, "time": int(bars[index].opened_at.timestamp()),
                "confirmation_time": int(bars[confirmation].opened_at.timestamp()),
                "observed_at": iso(bars[index].closed_at), "confirmed_at": iso(bars[confirmation].closed_at)}
        pivots.append(item)
    # Build waves only between adjacent independently confirmed raw pivots.
    # A later same-side extreme starts the next leg; it never rewrites a
    # previously published leg. This local pairing survives dropped old pivots.
    chain, swings = [], []
    for pivot in pivots:
        if not chain:
            chain.append(pivot)
            continue
        previous = chain[-1]
        distance = abs(pivot["price"] - previous["price"])
        if (pivot["kind"] == previous["kind"] or distance < volatility[pivot["index"]] * PARAMETERS["minimum_swing_atr"]):
            chain.append(pivot)
            continue
        chain.append(pivot)
        start, end = previous["index"], pivot["index"]
        # Non-overlapping wave volume counts the bars after the start pivot.
        volume = sum(bar.volume for bar in bars[start + 1:end + 1])
        duration = end - start
        scaled = distance / volatility[end] if volatility[end] > 0 else None
        swings.append({"swing_id": identity("swing", timeframe, previous["pivot_id"], pivot["pivot_id"]),
                       "start_pivot_id": previous["pivot_id"], "end_pivot_id": pivot["pivot_id"],
                       "start_time": previous["time"], "end_time": pivot["time"],
                       "confirmation_time": pivot["confirmation_time"],
                       "start_at": previous["observed_at"], "end_at": pivot["observed_at"],
                       "confirmed_at": pivot["confirmed_at"], "start_price": previous["price"],
                       "end_price": pivot["price"], "direction": "UP" if pivot["price"] > previous["price"] else "DOWN",
                       "distance": distance, "duration_bars": duration, "progress_atr": scaled,
                       "cumulative_volume": volume, "volume_per_bar": volume / duration,
                       "degree": "major" if scaled is not None and scaled >= PARAMETERS["major_swing_atr"] else "internal",
                       "status": "confirmed"})
    provisional = None
    if chain and chain[-1]["index"] < len(bars) - 1:
        start = chain[-1]
        provisional = {"start_time": start["time"], "end_time": int(bars[-1].opened_at.timestamp()),
                       "start_at": start["observed_at"], "end_at": iso(bars[-1].closed_at),
                       "start_price": start["price"], "end_price": bars[-1].close,
                       "direction": "UP" if bars[-1].close >= start["price"] else "DOWN",
                       "status": "unconfirmed", "confirmed_at": None, "degree": "internal"}
    return {"pivots": pivots, "swings": swings, "provisional_swing": provisional,
            "ambiguous_times": [iso(bars[index].closed_at) for index in sorted(ambiguous)],
            "atr": volatility}
