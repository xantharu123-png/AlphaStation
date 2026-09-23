"""Bounded v3 causal structure and independent entry-plan state machines.

Thresholds are frozen engineering assumptions, not fitted performance claims.
Volume-confirmed completed breaks authorize an entry candidate; an absent
retest is an explicit warning, never a fabricated LPS/LPSY event.
"""
from __future__ import annotations

import math

from modules.trade_levels import trade_geometry
from modules.breakout_warnings import breakout_warning_fields
from modules.wyckoff_swings import PARAMETERS, identity, iso, swing_evidence


MODEL = "causal_wyckoff_v3"


def _rvol(bars, index):
    history = [bar.volume for bar in bars[max(0, index - 20):index]]
    positives = [value for value in history if value > 0]
    if bars[index].volume <= 0 or len(positives) < max(5, math.ceil(len(history) * .8)):
        return None
    return bars[index].volume / (sum(positives) / len(positives))


def _phases(row, bars):
    events = row["events"]
    first = next(e for e in events if e["name"] in {"SC", "BC", "RangeOrigin"})
    reaction = next(e for e in events if e["name"] == "AR")
    st = next((e for e in events if e["name"] == "ST"), None)
    decisive = next((e for e in events if e["name"] in {"Spring", "UTAD", "CTest"}), None)
    progress = next((e for e in events if e["name"] in {"SOS", "SOW", "InRangeSOS", "InRangeSOW"}), None)
    follow = next((e for e in events if e["name"] == "EFollowThrough"), None)
    phases = []
    def add(name, start, proof, basis, confirmation_start=False):
        phases.append({"phase": name, "model": MODEL,
                       "start_time": start["confirmation_time"] if confirmation_start else start["time"],
                       "confirmed_time": proof["confirmation_time"],
                       "end_time": int(bars[-1].opened_at.timestamp()),
                       "observed_at": start["confirmed_at"] if confirmation_start else start["observed_at"],
                       "confirmed_at": proof["confirmed_at"], "basis": basis,
                       "status": "invalidated" if row["structure_state"] == "failed" else
                                 "developing" if name == "A" and st is None else "inferred",
                       "event_ids": [start["event_id"], proof["event_id"]]})
    add("A", first, st or reaction, "climax_reaction_first_secondary_test" if st and row["origin_kind"] == "climactic"
        else "climax_reaction_only" if row["origin_kind"] == "climactic" else "prior_trend_range_origin_reaction")
    if st:
        add("B", st, st, "range_after_first_secondary_test", True)
    if decisive:
        add("C", decisive, decisive, "higher_low_lower_high_test_and_directional_confirmation" if decisive["name"] == "CTest"
            else "optional_counter_boundary_test_and_recovery")
    if progress:
        add("D", progress, progress, "decisive_test_directional_in_range_progress" if progress["name"].startswith("InRange")
            else "volume_confirmed_range_break")
    if follow:
        add("E", follow, follow, "breakout_retest_and_confirmed_fresh_outside_progress")
    # Phase boundaries are interpretive; their confirmations are not backdated.
    for current, following in zip(phases, phases[1:]):
        current["end_time"] = max(current["confirmed_time"], following["start_time"])
    row.update(phase_evidence=phases, phase_basis="inferred_event_intervals",
               sequence_basis="chronological_display_ordinal_not_canonical_subwave", unmodelled_phases=[],
               model_scope="reversal_and_continuation", limitations=["phase_boundaries_inferred_not_exact",
               "quality_score_not_win_probability", "ohlc_intrabar_order_unknown", "finite_history_context_only"])


def _direction(bars, direction, tf, evidence):
    sign = 1 if direction == "LONG" else -1
    prices = [{"open": sign * b.open, "close": sign * b.close,
               "high": sign * (b.high if sign == 1 else b.low),
               "low": sign * (b.low if sign == 1 else b.high)} for b in bars]
    spreads = [b.high - b.low for b in bars]
    rvol = [_rvol(bars, i) for i in range(len(bars))]
    average = [sum(spreads[max(0, i - 20):i]) / min(i, 20) if i else 0 for i in range(len(bars))]
    low_kind, high_kind = ("LOW", "HIGH") if sign == 1 else ("HIGH", "LOW")
    lows = {p["index"]: p for p in evidence["pivots"] if p["kind"] == low_kind}
    low_confirmations = {}
    for pivot in lows.values():
        low_confirmations.setdefault(pivot["confirmation_index"], []).append(pivot["index"])
    highs = {p["index"]: p for p in evidence["pivots"] if p["kind"] == high_kind}
    ambiguous = set(evidence["ambiguous_times"])
    candidates = []

    def separate(previous, index, width):
        return (index - previous >= PARAMETERS["separate_test_bars"]
                and any(previous < p["index"] < index and p["confirmation_index"] <= index + 1
                        and sign * p["price"] - max(prices[previous]["low"], prices[index]["low"]) >= width * .20
                        for p in highs.values()))

    for origin in range(20, len(bars) - 5):
        candle = prices[origin]
        prior = prices[origin - 15:origin]
        change = (prior[-1]["close"] - prior[0]["close"]) / abs(prior[0]["close"])
        prior_high = max(p["high"] for p in prior)
        decline = (prior_high - candle["low"]) / abs(prior_high)
        position = (candle["close"] - candle["low"]) / spreads[origin] if spreads[origin] else 0
        climactic = (change <= -PARAMETERS["prior_trend_fraction"]
                     and rvol[origin] is not None and rvol[origin] >= 1.8 and spreads[origin] >= average[origin] * 1.3
                     and position >= .45 and decline >= .05 and prior[-1]["close"] > candle["close"])
        continuation = change >= PARAMETERS["prior_trend_fraction"]
        nonclimactic = (not climactic and abs(change) >= PARAMETERS["prior_trend_fraction"]
                       and origin in lows and rvol[origin] is not None
                       and (continuation or change <= -PARAMETERS["prior_trend_fraction"]))
        if not climactic and not nonclimactic:
            continue
        if iso(bars[origin].closed_at) in ambiguous:
            continue
        kind = "climactic" if climactic else "continuation" if continuation else "nonclimactic"
        # Nonclimactic origins need own contraction and repeated boundary proof;
        # no relaxed climax-volume shortcut is used.
        if nonclimactic and spreads[origin] > average[origin] * 1.3:
            continue
        ar, st, st_confirmed = None, None, None
        for confirmed in range(origin + 3, len(bars)):
            index = confirmed - 1
            if (index < origin + 20 and bars[index].volume > 0 and bars[confirmed].volume > 0
                    and iso(bars[index].closed_at) not in ambiguous
                    and prices[index]["high"] >= prices[index - 1]["high"]
                    and prices[index]["high"] > prices[confirmed]["high"]
                    and (ar is None or prices[index]["high"] > prices[ar]["high"])):
                ar = index
            if ar is None:
                continue
            width = prices[ar]["high"] - candle["low"]
            for tested in low_confirmations.get(confirmed, []):
                if (tested >= ar + 3
                        and candle["low"] - width * .02 <= prices[tested]["low"] <= candle["low"] + width * .25
                        and prices[tested]["close"] >= candle["low"] and prices[confirmed]["close"] > prices[tested]["close"]
                        and bars[confirmed].volume > 0 and rvol[tested] is not None
                        and rvol[tested] <= min(1.2, rvol[origin] * .75) and spreads[tested] <= spreads[origin]):
                    st, st_confirmed = tested, confirmed
                    break
            if st is not None:
                break
        if ar is None:
            continue
        lower, upper = candle["low"], prices[ar]["high"]
        width, midpoint = upper - lower, abs((upper + lower) / 2)
        if width <= 0 or not midpoint or not .02 < width / midpoint < .30:
            continue
        if nonclimactic:
            # Only observed prior trend plus a mature two-sided range qualifies.
            if st is None or st - origin < PARAMETERS["nonclimactic_range_bars"]:
                continue
            oscillations = [p for p in highs.values() if ar <= p["index"] < st
                            and sign * p["price"] >= lower + width * .55]
            if len(oscillations) < 2 or any(p["close"] < lower for p in prices[origin + 1:st_confirmed + 1]):
                continue
        typ = ("Reaccumulation" if sign == 1 else "Redistribution") if kind == "continuation" else (
              "Accumulation" if sign == 1 else "Distribution")
        sid = identity("structure", MODEL, tf, direction, kind, iso(bars[origin].closed_at))
        events = []
        row = {"model": MODEL, "timeframe": tf, "direction": direction, "type": typ, "structure_type": typ,
               "structure_id": sid, "parent_structure_id": None, "structure_state": "developing",
               "origin_kind": kind, "phase": "A", "variant": "no_spring", "trade_ready": False,
               "interpretation_state": "unresolved_until_directional_breakout",
               "entry_state": "no_trigger", "signal_state": "context", "score": 35,
               "score_kind": "quality_not_probability", "events": events, "entry_trigger": None, "entry_triggers": [],
               "range_low": sign * lower if sign == 1 else sign * upper,
               "range_high": sign * upper if sign == 1 else sign * lower,
               "range_start_time": int(bars[origin].opened_at.timestamp()),
               "range_end_time": int(bars[-1].opened_at.timestamp()),
               "range_confirmed_at": iso(bars[ar + 1].closed_at),
               "range_confirmed_time": int(bars[ar + 1].opened_at.timestamp()),
               "signal_confirmed_at": None, "latest_completed_at": iso(bars[-1].closed_at),
               "invalidation_reason": None, "structure_failure_reason": None, "structure_failed_at": None, "trade": None,
               "prior_trend": {"direction": "UP" if sign * change > 0 else "DOWN", "fraction": abs(change),
                               "start_at": iso(bars[origin - 15].closed_at), "end_at": iso(bars[origin - 1].closed_at)},
               "history_context": {"status": "complete", "available_bars": len(bars), "prior_trend_bars": 15,
                                   "window_start_at": iso(bars[0].closed_at), "scope": "required_local_anchors_only"}}

        def event(name, index, confirmed, price, phase, *, publish=True):
            actual = {"SC": "SC" if sign == 1 else "BC", "Spring": "Spring" if sign == 1 else "UTAD",
                      "SOS": "SOS" if sign == 1 else "SOW", "LPS": "LPS" if sign == 1 else "LPSY",
                      "InRangeSOS": "InRangeSOS" if sign == 1 else "InRangeSOW",
                      "PS": "PS" if sign == 1 else "PSY"}.get(name, name)
            result = {"name": actual, "index": index, "time": int(bars[index].opened_at.timestamp()),
                      "confirmation_time": int(bars[confirmed].opened_at.timestamp()),
                      "observed_at": iso(bars[index].closed_at), "confirmed_at": iso(bars[confirmed].closed_at),
                      "price": sign * price, "volume_ratio": rvol[index], "phase": phase,
                      "event_id": identity("event", sid, actual, iso(bars[index].closed_at))}
            if publish:
                events.append(result)
            return result

        first = event("SC" if climactic else "RangeOrigin", origin, origin if climactic else lows[origin]["confirmation_index"], lower, "A")
        reaction = event("AR", ar, ar + 1, upper, "A")
        if climactic:
            preliminary = next((i for i in range(origin - 15, origin - 2)
                                if i in lows and rvol[i] is not None and rvol[i] >= 1.3
                                and prices[i]["low"] > lower and spreads[i] > 0
                                and (prices[i]["close"] - prices[i]["low"]) / spreads[i] >= .5), None)
            if preliminary is not None:
                event("PS", preliminary, lows[preliminary]["confirmation_index"], prices[preliminary]["low"], "A")
        candidates.append(row)

        def block(reason, state="invalid_geometry", structure=False, failed_index=None):
            row.update(signal_state="invalidated", invalidation_reason=reason, entry_state=state,
                       trade_ready=False, trade=None, signal_confirmed_at=None)
            if structure:
                if failed_index is None:
                    failed_index = next((i for i in range(origin + 1, len(bars)) if prices[i]["close"] < lower), origin)
                row.update(structure_state="failed", structure_failure_reason=reason,
                           structure_failed_at=iso(bars[failed_index].closed_at))

        if st is None:
            if any(p["close"] < lower for p in prices[origin + 1:]):
                block("range_failed_before_secondary_test", structure=True)
            continue
        if any(p["close"] < lower for p in prices[origin + 1:st_confirmed + 1]):
            block("range_failed_before_secondary_test", structure=True)
            continue
        secondary = event("ST", st, st_confirmed, prices[st]["low"], "A")
        row.update(phase="B", score=50)

        def breakout_trigger(proof):
            # Freeze structural risk at the completed breakout candle. A
            # future retest may nominate its own, independently checked stop.
            index = proof["index"]
            confirmation_atr = evidence["atr"][index]
            trigger = {
                "trigger_id": identity("trigger", sid, proof["event_id"], "confirmed_breakout"),
                "trigger_mode": "confirmed_breakout", "confirmed_at": proof["confirmed_at"],
                "event_ids": {"origin": first["event_id"], "reaction": reaction["event_id"],
                              "test": secondary["event_id"], "breakout": proof["event_id"]},
                "state": "ready", "reason": None, "terminal_at": None,
                "stop_atr": {"basis": "trailing_14_true_ranges", "value": confirmation_atr,
                             "previous_close_at": iso(bars[index - 14].closed_at),
                             "start_at": iso(bars[index - 13].closed_at), "confirmed_at": proof["confirmed_at"]},
                "stop": sign * (prices[index]["low"] - confirmation_atr * .25),
            }
            if not math.isfinite(confirmation_atr) or confirmation_atr <= 0:
                trigger.update(state="data_missing", reason="confirmation_atr_unavailable", stop=None)
                return trigger
            # Neither a later recovery nor an unobserved intrabar ordering can
            # resurrect a failed, stopped or already consumed breakout trigger.
            for later in range(index + 1, len(bars)):
                bar = bars[later]
                stopped = bar.low <= trigger["stop"] if sign == 1 else bar.high >= trigger["stop"]
                consumed = sign * (bar.high if sign == 1 else bar.low) >= upper + width * .75
                failed_break = prices[later]["close"] <= upper
                if stopped and consumed:
                    trigger.update(state="ambiguous", reason="ambiguous_no_intrabar_order")
                elif failed_break:
                    trigger.update(state="expired", reason="breakout_failed")
                elif stopped:
                    trigger.update(state="stopped", reason="post_confirmation_stop_breached")
                elif consumed:
                    trigger.update(state="target_passed", reason="projected_target_not_beyond_entry")
                else:
                    continue
                trigger["terminal_at"] = iso(bar.closed_at)
                break
            return trigger

        last_test, decisive, sos, lps = st, None, None, None
        pending_test, pending_decisive = None, None
        last_upper_test, last_spring_test = ar, None
        breakout_failed, skip_until = False, st_confirmed + 1
        for index in range(st_confirmed + 1, len(bars)):
            if index < skip_until:
                continue
            now = iso(bars[index].closed_at)
            if pending_test is not None and pending_test["confirmed_at"] <= now:
                events.append(pending_test)
                if sos is None:
                    secondary, last_test = pending_test, pending_test["index"]
                pending_test = None
            if pending_decisive is not None and pending_decisive["confirmed_at"] <= now:
                if sos is None:
                    decisive = pending_decisive
                    events.append(decisive)
                    row.update(phase="C", variant="without_spring", score=60)
                pending_decisive = None
            p = prices[index]
            if p["low"] < lower:
                recovery = next((j for j in range(index, min(index + 4, len(bars)))
                                 if bars[j].volume > 0 and prices[j]["close"] > lower + width * .10), None)
                if rvol[index] is None:
                    block("spring_volume_unavailable", "data_missing")
                    break
                if (rvol[index] >= .85 or lower - p["low"] > width * .20 or recovery is None):
                    if recovery is None and index + 3 >= len(bars) and rvol[index] < .85 and lower - p["low"] <= width * .20:
                        row["structure_state"] = "unclear"
                        block("counter_boundary_recovery_pending", "no_trigger")
                    else:
                        block("range_failed", structure=True, failed_index=index)
                    break
                if sos is not None:
                    block("range_failed", structure=True, failed_index=index)
                    break
                if iso(bars[index].closed_at) in ambiguous:
                    block("ambiguous_intrabar_order", "data_missing")
                    break
                if index - last_test >= 3 and separate(last_test, index, width):
                    decisive = event("Spring", index, recovery, p["low"], "C")
                    row.update(phase="C", variant="spring", score=60)
                    last_test = index
                skip_until = (recovery or index) + 1
                continue
            if sos is not None and p["close"] < upper:
                breakout_failed = True
            if (sos is None and index in highs and index - last_upper_test >= 3
                    and highs[index]["confirmation_index"] < len(bars)
                    and upper - width * .20 <= p["high"] <= upper and rvol[index] is not None
                    and any(last_upper_test < q["index"] < index and sign * q["price"] < lower + width * .5
                            for q in lows.values())):
                event("UpperTest" if sign == 1 else "LowerTest", index,
                      highs[index]["confirmation_index"], p["high"], "B")
                last_upper_test = index
            if sos is None and index in lows and index + 1 < len(bars):
                confirmation = lows[index]["confirmation_index"]
                low_volume = rvol[index] is not None and rvol[index] <= 1.2 and bars[confirmation].volume > 0
                distinct = separate(last_test, index, width)
                if (decisive is not None and decisive["name"] in {"Spring", "UTAD"}
                        and index > decisive["index"] and iso(bars[index].closed_at) > decisive["confirmed_at"]
                        and low_volume and rvol[index] < .85 and p["low"] > sign * decisive["price"]
                        and p["low"] <= lower + width * .35 and prices[confirmation]["close"] > p["close"]
                        and separate(last_spring_test or decisive["index"], index, width)):
                    event("SpringTest" if sign == 1 else "UTADTest", index, confirmation, p["low"], "C")
                    last_spring_test = index
                if (low_volume and distinct and lower - width * .02 <= p["low"] <= lower + width * .25
                        and prices[confirmation]["close"] > p["close"]):
                    pending_test = event("ST", index, confirmation, p["low"], "B", publish=False)
                elif (decisive is None and low_volume and distinct and rvol[index] < .9
                      and lower + width * .25 < p["low"] <= lower + width * .55
                      and p["low"] > prices[last_test]["low"] + width * .1):
                    # A higher low alone is not C: wait for a close above the
                    # last intervening reaction high, still within the range.
                    counter = max((sign * q["price"] for q in highs.values()
                                   if last_test < q["index"] < index and q["confirmation_index"] <= index), default=upper)
                    follow = next((j for j in range(confirmation, min(index + 7, len(bars)))
                                   if bars[j].volume > 0 and prices[j]["close"] > counter
                                   and prices[j]["close"] < upper), None)
                    if follow is not None:
                        pending_decisive = event("CTest", index, follow, p["low"], "C", publish=False)
            if (sos is None and decisive is not None and index > decisive["index"]
                    and iso(bars[index].closed_at) > decisive["confirmed_at"]
                    and index in lows and index + 1 < len(bars)
                    and lower + width * .4 <= p["low"] < upper
                    and p["low"] > sign * decisive["price"] + width * .1
                    and rvol[index] is not None and rvol[index] < 1.2
                    and prices[index + 1]["close"] > p["close"]
                    and not any(e["name"] in {"InRangeSOS", "InRangeSOW"} for e in events)):
                event("InRangeSOS", index, index + 1, p["low"], "D")
                row.update(phase="D", score=70)
            if (sos is None and p["close"] > upper and p["close"] > p["open"]
                    and iso(bars[index].closed_at) > secondary["confirmed_at"]
                    and rvol[index] is not None and rvol[index] >= 1.5
                    and spreads[index] >= average[index] * 1.3
                    and (p["close"] - p["low"]) / spreads[index] >= .65
                    and iso(bars[index].closed_at) not in ambiguous):
                sos = event("SOS", index, index, p["close"], "D")
                row.update(phase="D", structure_state="confirmed", interpretation_state="direction_confirmed",
                           score=min(80, row["score"] + 20))
                row["entry_triggers"].append(breakout_trigger(sos))
                continue
            if sos is None or index <= sos["index"] or breakout_failed or index + 1 >= len(bars):
                continue
            if (index in lows and lows[index]["confirmation_index"] == index + 1
                    and upper - width * .10 <= p["low"] <= upper + width * .10
                    and p["low"] < sign * sos["price"] and p["close"] >= upper
                    and prices[index + 1]["close"] > p["close"] and bars[index + 1].volume > 0
                    and rvol[index] is not None and rvol[index] < .9
                    and spreads[index] <= spreads[sos["index"]]
                    and (lps is None or separate(lps["index"], index, width))):
                previous_lps = lps
                lps = event("LPS", index, index + 1, p["low"], "D" if row["phase"] != "E" else "E")
                if previous_lps is not None:
                    backup = event("Backup", index, index + 1, p["low"], lps["phase"])
                    backup["related_event_id"] = lps["event_id"]
                # A Wilder seed over the entire supplied window repaints risk
                # after irrelevant old bars roll out. Freeze a documented local
                # 14-TR support, including the preceding close, at confirmation.
                confirmation_atr = evidence["atr"][index + 1]
                trigger = {"trigger_id": identity("trigger", sid, sos["event_id"], lps["event_id"]),
                           "trigger_mode": "confirmed_retest",
                           "confirmed_at": lps["confirmed_at"],
                           "event_ids": {"origin": first["event_id"], "reaction": reaction["event_id"],
                                         "test": secondary["event_id"], "breakout": sos["event_id"], "retest": lps["event_id"]},
                           "state": "ready", "reason": None, "terminal_at": None,
                           "stop_atr": {"basis": "trailing_14_true_ranges", "value": confirmation_atr,
                                        "previous_close_at": iso(bars[index - 13].closed_at),
                                        "start_at": iso(bars[index - 12].closed_at),
                                        "confirmed_at": lps["confirmed_at"]},
                           "stop": sign * (p["low"] - confirmation_atr * .25)}
                if not math.isfinite(confirmation_atr) or confirmation_atr <= 0:
                    trigger.update(state="data_missing", reason="confirmation_atr_unavailable", stop=None)
                else:
                    # Earliest terminal evidence is immutable. OHLC cannot
                    # order stop and target inside the same completed candle.
                    for later in range(index + 2, len(bars)):
                        b = bars[later]
                        stopped = b.low <= trigger["stop"] if sign == 1 else b.high >= trigger["stop"]
                        consumed = sign * (b.high if sign == 1 else b.low) >= upper + width * .75
                        failed_break = prices[later]["close"] < upper
                        if stopped and consumed:
                            trigger.update(state="ambiguous", reason="ambiguous_no_intrabar_order")
                        elif failed_break:
                            trigger.update(state="expired", reason="breakout_failed")
                        elif stopped:
                            trigger.update(state="stopped", reason="post_confirmation_stop_breached")
                        elif consumed:
                            trigger.update(state="target_passed", reason="projected_target_not_beyond_entry")
                        else:
                            continue
                        trigger["terminal_at"] = iso(b.closed_at)
                        break
                row["entry_triggers"].append(trigger)
                row["score"] = min(100, row["score"] + 20)
            # Phase E needs a completed, confirmed impulse after a retest. Time
            # passing or a lone last-bar price spike never supplies this proof.
            if (lps is not None and row["phase"] != "E" and index > lps["index"] + 1
                    and p["close"] > max(prices[j]["high"] for j in range(sos["index"], lps["index"] + 1)) + width * .10
                    and prices[index + 1]["close"] >= p["close"] and prices[index + 1]["low"] > upper
                    and bars[index].volume > 0 and bars[index + 1].volume > 0):
                event("EFollowThrough", index, index + 1, p["close"], "E")
                row.update(phase="E", structure_state="continuation")
        if row["structure_state"] == "failed" or row["entry_state"] == "data_missing":
            continue
        if breakout_failed:
            for trigger in row["entry_triggers"]:
                if trigger["state"] == "ready":
                    trigger.update(state="expired", reason="breakout_failed")
            if row["entry_triggers"]:
                active = row["entry_triggers"][-1]
                row["entry_trigger"] = dict(active)
                block(active["reason"], active["state"])
            else:
                block("breakout_failed", "expired")
            continue
        if not row["entry_triggers"]:
            continue
        active = row["entry_triggers"][-1]
        row["entry_trigger"] = dict(active)
        if active["state"] != "ready":
            block(active["reason"], active["state"])
            continue
        if bars[-1].volume <= 0:
            block("latest_volume_evidence_missing", "data_missing")
            continue
        by_id = {e["event_id"]: e for e in events}
        if any(by_id[anchor]["volume_ratio"] is None or by_id[anchor]["volume_ratio"] <= 0
               for anchor in active["event_ids"].values()):
            block("trigger_volume_evidence_missing", "data_missing")
            continue
        entry, stop = bars[-1].close, active["stop"]
        tp1, tp2 = sign * (upper + width * .75), sign * (upper + width * 1.5)
        geometry = trade_geometry(entry, stop, tp1, tp2, direction)
        if not geometry["valid"]:
            passed = sign * (tp1 - entry) <= 0
            block("projected_target_not_beyond_entry" if passed else "invalid_trade_geometry", "target_passed" if passed else "invalid_geometry")
            continue
        row.update(trade_ready=True, entry_state="ready", signal_state="confirmed",
                   signal_confirmed_at=active["confirmed_at"],
                   trade={"entry": entry, "stop": stop, "tp1": tp1, "tp2": tp2, "rr": geometry["rr_tp1"],
                          "direction": direction, "target_basis": "measured_range_projection", "fill_evidence_verified": False})
    return candidates[-PARAMETERS["maximum_contexts_per_direction"]:]


def detect_structures(bars, timeframe):
    evidence = swing_evidence(bars, timeframe)
    patterns = _direction(bars, "LONG", timeframe, evidence) + _direction(bars, "SHORT", timeframe, evidence)
    # Parentage uses only an already confirmed E at the child's origin. Later
    # success cannot retrospectively manufacture a parent or change its ID.
    for row in sorted(patterns, key=lambda p: p["range_start_time"]):
        parents = [p for p in patterns if p is not row and p["direction"] == row["direction"]
                   and p["range_start_time"] < row["range_start_time"]
                   and (p["structure_failed_at"] is None or p["structure_failed_at"] > row["events"][0]["observed_at"])
                   and (row["range_high"] - row["range_low"]) < (p["range_high"] - p["range_low"])
                   and any(e["name"] == "EFollowThrough" and e["confirmation_time"] < row["range_start_time"] for e in p["events"])]
        if parents:
            row["parent_structure_id"] = max(parents, key=lambda p: p["range_start_time"])["structure_id"]
        occurrences = {}
        row["events"].sort(key=lambda e: (e["confirmation_time"], e["time"], e["name"]))
        for sequence, event in enumerate(row["events"], 1):
            occurrences[event["name"]] = occurrences.get(event["name"], 0) + 1
            event.update(sequence=sequence, occurrence=occurrences[event["name"]])
        row["swings"] = [dict(s) for s in evidence["swings"] if s["start_time"] >= row["range_start_time"]]
        row["provisional_swing"] = evidence["provisional_swing"]
        _phases(row, bars)
    ready_directions = {p["direction"] for p in patterns if p["trade_ready"]}
    if len(ready_directions) > 1:
        for p in patterns:
            if p["trade_ready"]:
                p.update(trade_ready=False, entry_state="conflict", signal_state="invalidated", trade=None,
                         signal_confirmed_at=None, invalidation_reason="conflicting_directional_patterns")
    for row in patterns:
        row.update(breakout_warning_fields(row["trade_ready"] is True
            and (row.get("entry_trigger") or {}).get("trigger_mode") == "confirmed_breakout"))
    patterns.sort(key=lambda p: (p["trade_ready"], p["structure_state"] != "failed", p["range_start_time"], p["score"]), reverse=True)
    return patterns, evidence
