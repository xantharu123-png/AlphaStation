"""Pure, completed-bar Wyckoff event detector (heuristics, not win probabilities).

The model recognizes a conservative SC/BC -> AR -> ST -> SOS/SOW -> LPS/LPSY
sequence. A Spring/UTAD is optional. Earlier phases remain chart context, never
an entry signal. Thresholds are explicit model assumptions, not calibrated edge.
Callers retain their own structural, execution, risk and mail-delivery gates.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
import re
from typing import Any, Mapping, Sequence

from modules.indicators import calculate_atr_14
from modules.level_zones import CompletedBar, normalize_completed_bars
from modules.trade_levels import trade_geometry


MODEL = "causal_wyckoff_v2"
UTC = timezone.utc


def _iso(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _utc(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("as_of must have an explicit timezone")
        return value.astimezone(UTC)
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("as_of must have an explicit timezone")
        return parsed.astimezone(UTC)
    raise ValueError("as_of must be an explicit timezone-aware datetime or ISO timestamp")


def _completed_input(raw_bars, *, cutoff, timeframe, timestamp_mode):
    """Use the canonical clock, but do not silently fabricate/drop bad evidence."""
    completed = []
    for raw in raw_bars:
        if isinstance(raw, CompletedBar):
            try:
                opened, closed = _utc(raw.opened_at), _utc(raw.closed_at)
            except (TypeError, ValueError, OverflowError):
                return (), "invalid_bar_timestamp"
            if closed > cutoff:
                continue
            if opened > closed:
                return (), "invalid_bar_timestamp"
            clock = CompletedBar(opened, closed, raw.open, raw.high, raw.low, raw.close, raw.volume)
            values = (raw.open, raw.high, raw.low, raw.close, raw.volume)
        elif isinstance(raw, Mapping):
            # A harmless OHLCV clock probe distinguishes unfinished/future bars
            # from malformed *completed* price/volume data before normalization.
            probe = {**raw, "open": 1., "high": 1., "low": 1., "close": 1., "volume": 1.}
            clocks = normalize_completed_bars([probe], timeframe=timeframe, as_of=cutoff,
                                             timestamp_mode=timestamp_mode)
            if not clocks:
                flag = next((raw[key] for key in ("is_closed", "complete", "completed", "final") if key in raw), None)
                if flag is not None and str(flag).lower() in {"false", "0", "no", "n", "open"}:
                    continue
                # Valid clocks beyond the requested cutoff are ignored. A row
                # without a verifiable clock cannot participate in this model.
                future = normalize_completed_bars([probe], timeframe=timeframe,
                    as_of=datetime.max.replace(tzinfo=UTC), timestamp_mode=timestamp_mode)
                if future:
                    continue
                return (), "invalid_bar_timestamp"
            clock = clocks[0]
            values = tuple(raw.get(long, raw.get(short)) for long, short in (
                ("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"), ("volume", "v")))
        else:
            return (), "invalid_bar_payload"
        try:
            if any(isinstance(value, bool) for value in values):
                raise ValueError
            open_, high, low, close, volume = (float(value) for value in values)
            if not all(math.isfinite(value) for value in (open_, high, low, close, volume)):
                raise ValueError
            if volume < 0:
                return (), "invalid_bar_volume"
            if min(open_, high, low, close) <= 0 or high < max(open_, close, low) or low > min(open_, close, high):
                return (), "invalid_bar_prices"
        except (TypeError, ValueError, OverflowError):
            return (), "invalid_bar_value"
        completed.append(CompletedBar(clock.opened_at, clock.closed_at, open_, high, low, close, volume))
    by_close = {}
    for bar in completed:
        signature = (bar.opened_at, bar.open, bar.high, bar.low, bar.close, bar.volume)
        previous = by_close.get(bar.closed_at)
        if previous is not None and previous[0] != signature:
            return (), "conflicting_completed_bars"
        by_close[bar.closed_at] = (signature, bar)
    return tuple(by_close[key][1] for key in sorted(by_close)), None


def _relative_volume(bars, index):
    if bars[index].volume <= 0:
        return None
    history = [bar.volume for bar in bars[max(0, index - 20):index]]
    positives = [value for value in history if value > 0]
    if len(positives) < max(5, math.ceil(len(history) * .8)):
        return None
    return bars[index].volume / (sum(positives) / len(positives))


def _oriented(bar, sign):
    return {"open": sign * bar.open, "close": sign * bar.close,
            "high": sign * (bar.high if sign == 1 else bar.low),
            "low": sign * (bar.low if sign == 1 else bar.high)}


def _annotate_evidence(row, bars):
    """Describe detected evidence, never manufacture canonical subwaves.

    A/B/C/D are interpretive event intervals, not exact historical boundaries.
    The first ST belongs to stopping action in A; its completed confirmation
    starts our inferred B interval. An absent Spring/UTAD does not manufacture C.
    """
    occurrences = {}
    for sequence, item in enumerate(row["events"], 1):
        name = item["name"]
        occurrences[name] = occurrences.get(name, 0) + 1
        item.update(sequence=sequence, occurrence=occurrences[name])
    events = row["events"]
    sc, ar = events[:2]
    st = next((item for item in events if item["name"] == "ST"), None)
    spring = next((item for item in events if item["name"] in {"Spring", "UTAD"}), None)
    sos = next((item for item in events if item["name"] in {"SOS", "SOW"}), None)
    phases = []

    def phase(name, first, proof, basis, *, confirmation_start=False):
        phases.append({"phase": name,
                       "start_time": first["confirmation_time"] if confirmation_start else first["time"],
                       "confirmed_time": proof["confirmation_time"],
                       "end_time": int(bars[-1].opened_at.timestamp()),
                       "observed_at": first["confirmed_at"] if confirmation_start else first["observed_at"],
                       "confirmed_at": proof["confirmed_at"],
                       "status": "invalidated" if row["signal_state"] == "invalidated" else
                                 ("developing" if name == "A" and st is None else "inferred"),
                       "basis": basis})

    phase("A", sc, st or ar, "climax_reaction_first_secondary_test" if st else "climax_reaction_only")
    if st:
        phase("B", st, st, "range_after_first_secondary_test", confirmation_start=True)
    if spring:
        phase("C", spring, spring, "optional_counter_boundary_test_and_recovery")
    if sos:
        phase("D", sos, sos, "volume_confirmed_range_break")
    for current, following in zip(phases, phases[1:]):
        current["end_time"] = following["start_time"]
    row.update(phase_evidence=phases, phase_basis="inferred_event_intervals",
               sequence_basis="chronological_display_ordinal_not_canonical_subwave",
               model_scope="climax_reversal", unmodelled_phases=["E"],
               limitations=["preliminary_support_supply_not_modelled", "reaccumulation_redistribution_not_modelled",
                            "unclimactic_variants_not_modelled", "phase_boundaries_inferred_not_exact",
                            "quality_score_not_win_probability"])


def _detect_direction(bars, direction, atr):
    sign = 1 if direction == "LONG" else -1
    prices = [_oriented(bar, sign) for bar in bars]
    spreads = [bar.high - bar.low for bar in bars]
    rvol = [_relative_volume(bars, index) for index in range(len(bars))]
    average_spread = [sum(spreads[max(0, index - 20):index]) / min(20, index) if index else 0.
                      for index in range(len(bars))]
    names = {"SC": "SC" if sign == 1 else "BC", "AR": "AR", "ST": "ST",
             "Spring": "Spring" if sign == 1 else "UTAD", "SOS": "SOS" if sign == 1 else "SOW",
             "LPS": "LPS" if sign == 1 else "LPSY"}

    def event(name, observed, confirmed, price):
        return {"name": names[name], "index": observed, "time": int(bars[observed].opened_at.timestamp()),
                "confirmation_time": int(bars[confirmed].opened_at.timestamp()),
                "observed_at": _iso(bars[observed].closed_at),
                "confirmed_at": _iso(bars[confirmed].closed_at),
                "price": sign * price, "volume_ratio": rvol[observed],
                # The first secondary test completes stopping action (A).
                # Phase B is inferred from its confirmation, not from the
                # occurrence of the test itself.
                "phase": {"SC": "A", "AR": "A", "ST": "A", "Spring": "C",
                          "SOS": "D", "LPS": "D"}[name]}

    candidates = []
    for sc in range(20, len(bars) - 5):
        candle = prices[sc]
        position = (candle["close"] - candle["low"]) / spreads[sc] if spreads[sc] else 0.
        prior_high = max(bar["high"] for bar in prices[sc - 15:sc])
        decline = (prior_high - candle["low"]) / abs(prior_high) if prior_high else 0.
        if (rvol[sc] is None or rvol[sc] < 1.8 or spreads[sc] < average_spread[sc] * 1.3
                or position < .45 or decline < .05 or prices[sc - 1]["close"] <= candle["close"]):
            continue
        # Form the range chronologically: a small first local peak is not
        # irrevocably the automatic rally/reaction while it is still unfolding.
        # Only completed right-hand confirmations may update its outer pivot.
        # The first confirmed secondary test freezes that pivot; neither a
        # later high nor a later failed breakout may rewrite a proven range.
        ar, st = None, None
        for confirmed in range(sc + 3, len(bars)):
            index = confirmed - 1
            if (index < sc + 20 and bars[index].volume > 0 and bars[confirmed].volume > 0
                    and prices[index]["high"] >= prices[index - 1]["high"]
                    and prices[index]["high"] > prices[confirmed]["high"]
                    and (ar is None or prices[index]["high"] > prices[ar]["high"])):
                ar = index
            if ar is None or index < ar + 3:
                continue
            tentative_width = prices[ar]["high"] - candle["low"]
            if (candle["low"] - tentative_width * .02 <= prices[index]["low"] <= candle["low"] + tentative_width * .25
                    and prices[index]["close"] >= candle["low"]
                    and prices[confirmed]["close"] > prices[index]["close"]
                    and bars[confirmed].volume > 0
                    and rvol[index] is not None and rvol[index] <= min(1.2, rvol[sc] * .75)
                    and spreads[index] <= spreads[sc]):
                st = index
                break
        if ar is None:
            continue
        lower, upper = candle["low"], prices[ar]["high"]
        width = upper - lower
        midpoint = abs((upper + lower) / 2)
        if width <= 0 or not midpoint or not .02 < width / midpoint < .30:
            continue
        events = [event("SC", sc, sc, lower), event("AR", ar, ar + 1, upper)]
        row = {"direction": direction, "type": "Accumulation" if sign == 1 else "Distribution",
               "phase": "A", "variant": "no_spring", "trade_ready": False,
               "signal_state": "context", "score": 35, "score_kind": "quality_not_probability",
               "events": events, "range_low": lower if sign == 1 else -upper,
               "range_high": upper if sign == 1 else -lower,
               "range_start_time": int(bars[sc].opened_at.timestamp()),
               "range_end_time": int(bars[-1].opened_at.timestamp()),
               "range_confirmed_at": _iso(bars[ar + 1].closed_at),
               "range_confirmed_time": int(bars[ar + 1].opened_at.timestamp()),
               "signal_confirmed_at": None, "latest_completed_at": _iso(bars[-1].closed_at),
               "invalidation_reason": None, "trade": None}
        candidates.append(row)

        def invalidate(reason):
            row.update(signal_state="invalidated", invalidation_reason=reason,
                       trade_ready=False, trade=None, signal_confirmed_at=None)

        if st is None:
            if any(bar["close"] < lower for bar in prices[sc + 1:]):
                invalidate("range_failed_before_secondary_test")
            continue
        # SC/BC establishes the outer boundary, not AR. A failed climax
        # cannot be resurrected by a later rally/reaction and secondary test.
        if any(bar["close"] < lower for bar in prices[sc + 1:st + 2]):
            invalidate("range_failed_before_secondary_test")
            continue
        events.append(event("ST", st, st + 1, prices[st]["low"]))
        row.update(phase="B", score=50)
        sos = None
        broken = False
        index = st + 2
        while index < len(bars):
            if prices[index]["low"] < lower:
                # A zero-volume wick is not low-volume absorption. A true
                # counter-boundary excursion needs a completed, timely recovery.
                if rvol[index] is None:
                    invalidate("spring_volume_unavailable")
                    broken = True
                    break
                recovery = next((following for following in range(index, min(index + 4, len(bars)))
                                 if bars[following].volume > 0
                                 and prices[following]["close"] > lower + width * .10), None)
                if (rvol[index] >= .85 or lower - prices[index]["low"] > width * .20
                        or recovery is None):
                    invalidate("range_failed")
                    broken = True
                    break
                events.append(event("Spring", index, recovery, prices[index]["low"]))
                row.update(phase="C", variant="spring", score=60)
                index = recovery + 1
            else:
                if (prices[index]["close"] > upper
                        and prices[index]["close"] > prices[index]["open"]
                        and rvol[index] is not None and rvol[index] >= 1.5
                        and spreads[index] >= average_spread[index] * 1.3
                        and (prices[index]["close"] - prices[index]["low"]) / spreads[index] >= .65):
                    sos = index
                    break
                index += 1
        if broken:
            continue
        if sos is None:
            continue
        events.append(event("SOS", sos, sos, prices[sos]["close"]))
        row.update(phase="D", score=row["score"] + 20)
        failed_at = next((index for index in range(sos + 1, len(bars))
                          if prices[index]["close"] < upper), None)
        # Preserve already confirmed LPS evidence after a later failure, but
        # do not build an LPS from candles after that first failed breakout.
        lps_end = min(len(bars) - 1, failed_at - 1) if failed_at is not None else len(bars) - 1
        lps = next((index for index in range(sos + 1, lps_end)
                    if upper - width * .10 <= prices[index]["low"] <= upper + width * .10
                    and prices[index]["low"] < prices[sos]["close"]
                    and prices[index]["close"] >= upper
                    and prices[index + 1]["close"] > prices[index]["close"]
                    and bars[index + 1].volume > 0
                    and rvol[index] is not None and rvol[index] < .9
                    and spreads[index] <= spreads[sos]), None)
        if lps is None:
            if failed_at is not None:
                invalidate("breakout_failed")
            continue
        events.append(event("LPS", lps, lps + 1, prices[lps]["low"]))
        row["score"] = min(100, row["score"] + 20)
        if failed_at is not None:
            invalidate("breakout_failed")
            continue
        entry = bars[-1].close
        # Freeze invalidation geometry at signal confirmation. Later volatility
        # must not widen a past stop or make a stopped-out setup look alive.
        confirmation_atr, _ = calculate_atr_14([bar.to_dict() for bar in bars[:lps + 2]])
        if not math.isfinite(confirmation_atr) or confirmation_atr <= 0:
            invalidate("confirmation_atr_unavailable")
            continue
        stop = sign * (prices[lps]["low"] - confirmation_atr * .25)
        if any((bar.low <= stop if sign == 1 else bar.high >= stop) for bar in bars[lps + 2:]):
            invalidate("post_confirmation_stop_breached")
            continue
        if bars[-1].volume <= 0:
            invalidate("latest_volume_evidence_missing")
            continue
        tp1, tp2 = sign * (upper + width * .75), sign * (upper + width * 1.50)
        geometry = trade_geometry(entry, stop, tp1, tp2, direction)
        if not geometry["valid"]:
            invalidate("projected_target_not_beyond_entry" if sign * (tp1 - entry) <= 0 else "invalid_trade_geometry")
            continue
        row.update(trade_ready=True, signal_state="confirmed",
                   signal_confirmed_at=_iso(bars[lps + 1].closed_at),
                   trade={"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2,
                          "rr": geometry["rr_tp1"], "direction": direction,
                          "target_basis": "measured_range_projection", "fill_evidence_verified": False})
    if not candidates:
        return None
    # A confirmed live pattern outranks context; a failed historic schematic
    # cannot conceal a later valid pattern. No directional winner is chosen
    # before the caller's LONG/SHORT filter.
    return max(candidates, key=lambda row: (row["trade_ready"], row["signal_state"] != "invalidated",
                                            row["range_start_time"], row["score"]))


def analyze_wyckoff(bars: Sequence[Mapping[str, Any]], *, as_of, timeframe: str,
                    direction: str = "ALL", timestamp_mode: str = "open", minimum_bars: int = 60):
    """Return causal chart context and separately gated Wyckoff entry evidence.

    Daily equity callers must supply actual market-session ``close_time``;
    this generic core never guesses an exchange session or reads a live clock.
    Missing/invalid volume blocks analysis, while actual zero-volume bars are
    retained as non-evidence (never interpreted as successful low-volume tests).
    """
    cutoff = _utc(as_of)
    tf = str(timeframe).strip().upper()
    if not re.fullmatch(r"[1-9][0-9]*[MHDW]", tf):
        raise ValueError("timeframe must be explicit, e.g. 1D, 4H or 15M")
    side = str(direction).strip().upper()
    if side not in {"LONG", "SHORT", "ALL"}:
        raise ValueError("direction must be LONG, SHORT or ALL")
    if timestamp_mode not in {"open", "close"}:
        raise ValueError("timestamp_mode must be open or close")
    result = {"model": MODEL, "status": "ok", "reason": None, "as_of": _iso(cutoff),
              "timeframe": tf, "bars_used": 0, "latest_completed_at": None, "patterns": []}
    completed, error = _completed_input(tuple(bars or ()), cutoff=cutoff, timeframe=tf,
                                        timestamp_mode=timestamp_mode)
    if error:
        result.update(status="invalid_data", reason=error)
        return result
    result.update(bars_used=len(completed), latest_completed_at=_iso(completed[-1].closed_at) if completed else None)
    if len(completed) < max(60, int(minimum_bars)):
        result.update(status="insufficient_data", reason="minimum_completed_bars_missing")
        return result
    if not any(bar.volume > 0 for bar in completed):
        result.update(status="invalid_data", reason="positive_volume_evidence_missing")
        return result
    atr, _ = calculate_atr_14([bar.to_dict() for bar in completed])
    if not math.isfinite(atr) or atr <= 0:
        result.update(status="invalid_data", reason="atr_unavailable")
        return result
    all_patterns = []
    for chosen in ("LONG", "SHORT"):
        pattern = _detect_direction(completed, chosen, atr)
        if pattern is not None:
            all_patterns.append(pattern)
    if sum(bool(pattern["trade_ready"]) for pattern in all_patterns) > 1:
        # The directional request cannot hide contradictory live evidence.
        # No score tie-breaker pretends to resolve competing causal sequences.
        for pattern in all_patterns:
            if pattern["trade_ready"]:
                pattern.update(trade_ready=False, signal_state="invalidated", trade=None,
                               signal_confirmed_at=None,
                               invalidation_reason="conflicting_directional_patterns")
    for pattern in all_patterns:
        _annotate_evidence(pattern, completed)
    result["patterns"] = [pattern for pattern in all_patterns if side == "ALL" or pattern["direction"] == side]
    return result


__all__ = ["MODEL", "analyze_wyckoff"]
