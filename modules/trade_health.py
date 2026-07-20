"""Central trade quality, fakeout and chase-risk guardrails.

The scanner scores answer "is this interesting?". This module answers the
separate execution question: "is it still tradeable right now?".

S-2 Audit-Fix 2026-06-10: Stop-Breach-Erkennung (current vs stop) ergaenzt.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from modules.trade_levels import trade_geometry


PREFERRED_MIN_RR = 1.5
HARD_MIN_RR = 1.0
MIN_RVOL = 0.7


def _lower_map(row: Dict[str, Any]) -> Dict[str, Any]:
    return {str(k).lower(): v for k, v in row.items()}


def _first(row: Dict[str, Any], aliases: Iterable[str], default: Any = None) -> Any:
    lower = _lower_map(row)
    for alias in aliases:
        if alias in row and row[alias] is not None:
            return row[alias]
        value = lower.get(str(alias).lower())
        if value is not None:
            return value
    return default


def _to_float(value: Any) -> Optional[float]:
    """Robust float parsing for scanner rows.

    N-toFloat Audit-Fix:
    - "1,5" wird als Dezimaltrenner gelesen (1.5), wenn kein Punkt vorhanden ist.
    - "1,234.56" / "1,234,567" behandeln Kommas als Tausendertrenner.
    - Nicht-numerische Strings wie "0x10" ergeben None (frueher faelschlich 10.0,
      weil jedes "x" entfernt wurde). Nur ein Suffix-"x" (z.B. "2.5x" RVOL) wird entfernt.
    """
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            text = value.replace("$", "").replace("%", "").strip()
            if text.lower().endswith("x"):
                text = text[:-1].strip()
            if "," in text:
                if "." not in text and text.count(",") == 1:
                    text = text.replace(",", ".")
                else:
                    text = text.replace(",", "")
            value = text
        number = float(value)
        if number != number:
            return None
        return number
    except Exception:
        return None


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "ja", "y", "hit", "confirmed"}


def _as_list(value: Any) -> List[str]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    if isinstance(value, tuple):
        return [str(v) for v in value if str(v).strip()]
    text = str(value).strip()
    if not text:
        return []
    for sep in ("|", ";"):
        if sep in text:
            return [part.strip() for part in text.split(sep) if part.strip()]
    return [text]


def _direction(row: Dict[str, Any], entry: Optional[float], stop: Optional[float]) -> str:
    raw = str(
        _first(
            row,
            ["direction", "Direction", "signal", "Signal", "BI_Direction", "side", "trend"],
            "",
        )
    ).upper()
    if "SHORT" in raw or "BEAR" in raw or raw in {"SELL", "PUT"}:
        return "SHORT"
    if "LONG" in raw or "BULL" in raw or raw in {"BUY", "CALL"}:
        return "LONG"
    if entry is not None and stop is not None:
        return "LONG" if stop < entry else "SHORT"
    return "LONG"


def _risk(entry: Optional[float], stop: Optional[float], direction: str) -> Optional[float]:
    if entry is None or stop is None:
        return None
    risk = entry - stop if direction == "LONG" else stop - entry
    return risk if risk and risk > 0 else None


def _target_missed(
    row: Dict[str, Any],
    current: Optional[float],
    direction: str,
    tp1: Optional[float],
    tp2: Optional[float],
) -> tuple[bool, bool]:
    tp1_missed = _to_bool(_first(row, ["tp1_missed", "late_to_tp1", "tp1_hit"], False))
    tp2_missed = _to_bool(_first(row, ["tp2_missed", "tp2_hit"], False))
    if current is not None:
        if direction == "LONG":
            tp1_missed = tp1_missed or (tp1 is not None and current >= tp1)
            tp2_missed = tp2_missed or (tp2 is not None and current >= tp2)
        else:
            tp1_missed = tp1_missed or (tp1 is not None and current <= tp1)
            tp2_missed = tp2_missed or (tp2 is not None and current <= tp2)
    return tp1_missed, tp2_missed


def _distance_to_entry_r(
    row: Dict[str, Any],
    current: Optional[float],
    entry: Optional[float],
    risk: Optional[float],
    direction: str,
) -> Optional[float]:
    provided = _to_float(_first(row, ["distance_to_entry_r", "entry_distance_r"]))
    if current is None or entry is None or risk is None or risk <= 0:
        return provided
    distance = (current - entry) / risk if direction == "LONG" else (entry - current) / risk
    return round(distance, 2)


def _live_rr(
    row: Dict[str, Any],
    current: Optional[float],
    entry: Optional[float],
    stop: Optional[float],
    tp1: Optional[float],
    tp2: Optional[float],
    direction: str,
) -> Optional[float]:
    # S-2 Audit-Fix: Wenn der Stop bereits gerissen ist, darf das Clamping
    # (max(current, entry) bzw. min(current, entry)) den Breach nicht maskieren.
    # Ein gerissener Stop bedeutet: kein lebendes Setup mehr -> Live R:R = 0.0.
    if current is not None and stop is not None:
        if direction == "LONG" and current <= stop:
            return 0.0
        if direction == "SHORT" and current >= stop:
            return 0.0
    levels_complete = all(value is not None for value in (entry, stop, tp1, tp2))
    if not levels_complete:
        # A stored scanner R:R is a plan metric, not a live execution metric.
        # Missing levels must therefore fail closed instead of reusing stale R:R.
        return None

    # Complete plans always use the shared signed geometry. A stale supplied
    # R:R must never make duplicate or wrong-side targets look tradeable.
    live_entry = entry
    if current is not None:
        live_entry = max(current, entry) if direction == "LONG" else min(current, entry)
    geometry = trade_geometry(live_entry, stop, tp1, tp2, direction)
    if not geometry.get("valid"):
        return 0.0
    return geometry.get("rr")


def _execution_cost_pct(row: Dict[str, Any], spread_pct: Optional[float]) -> Optional[float]:
    """Return estimated round-trip execution costs as percent of entry.

    Explicit all-in costs take precedence. Otherwise one full spread plus
    two-sided fees/slippage are combined. Missing inputs stay missing instead
    of inventing a market-independent default.
    """
    all_in = _to_float(
        _first(
            row,
            [
                "execution_cost_pct",
                "round_trip_cost_pct",
                "estimated_round_trip_cost_pct",
            ],
        )
    )
    if all_in is not None:
        return round(max(0.0, all_in), 6)

    spread_bps = _to_float(_first(row, ["spread_bps"]))
    spread_component = spread_pct
    if spread_component is None and spread_bps is not None:
        spread_component = spread_bps / 100.0

    round_trip_fee = _to_float(_first(row, ["round_trip_fee_pct", "fees_pct"]))
    if round_trip_fee is None:
        one_way_fee = _to_float(_first(row, ["fee_pct", "commission_pct"]))
        round_trip_fee = 2.0 * one_way_fee if one_way_fee is not None else None

    round_trip_slippage = _to_float(_first(row, ["round_trip_slippage_pct"]))
    if round_trip_slippage is None:
        one_way_slippage = _to_float(_first(row, ["slippage_pct"]))
        slippage_bps = _to_float(_first(row, ["slippage_bps"]))
        if one_way_slippage is not None:
            round_trip_slippage = 2.0 * one_way_slippage
        elif slippage_bps is not None:
            round_trip_slippage = 2.0 * slippage_bps / 100.0

    components = [
        value
        for value in (spread_component, round_trip_fee, round_trip_slippage)
        if value is not None
    ]
    if not components:
        return None
    return round(sum(max(0.0, value) for value in components), 6)


def _net_live_rr(
    current: Optional[float],
    entry: Optional[float],
    stop: Optional[float],
    tp1: Optional[float],
    tp2: Optional[float],
    direction: str,
    execution_cost_pct: Optional[float],
) -> Optional[float]:
    if execution_cost_pct is None or any(value is None for value in (entry, stop, tp1, tp2)):
        return None
    live_entry = entry
    if current is not None:
        live_entry = max(current, entry) if direction == "LONG" else min(current, entry)
    geometry = trade_geometry(live_entry, stop, tp1, tp2, direction)
    if not geometry.get("valid"):
        return 0.0
    cost_amount = live_entry * max(0.0, execution_cost_pct) / 100.0
    net_risk = geometry["risk"] + cost_amount
    net_reward = 0.5 * geometry["reward1"] + 0.5 * geometry["reward2"] - cost_amount
    if net_risk <= 0:
        return 0.0
    return round(max(0.0, net_reward) / net_risk, 2)


def _risk_band(score: int) -> str:
    if score >= 80:
        return "LOW"
    if score >= 65:
        return "MEDIUM"
    if score >= 50:
        return "HIGH"
    return "CRITICAL"


def _entry_quality(score: int) -> str:
    if score >= 85:
        return "GOOD"
    if score >= 70:
        return "EXTENDED"
    if score >= 50:
        return "LATE"
    return "CHASE"


def _decision_label(decision: str) -> str:
    return {
        "TRADEABLE": "Tradeable",
        "WAIT_FOR_RETEST": "Auf Retest warten",
        "WAIT_FOR_CONTINUATION": "Momentum-Fortsetzung beobachten",
        "WAIT_FOR_TRIGGER": "Setup gut, Trigger fehlt",
        "WATCH_ONLY": "Nur Watchlist",
        "NO_TRADE": "No Trade",
    }.get(decision, decision)


def _sanitize_trade_health_messages(
    positives: List[str],
    warnings: List[str],
    tactical_reasons: List[str],
    exclusion_reasons: List[str],
    *,
    decision: str,
    vol_confirmed_bool: Optional[bool],
    chase_risk: str,
    fakeout_risk: str,
) -> List[str]:
    """Remove positive snippets that contradict the final risk state."""
    risk_text = " | ".join(warnings + tactical_reasons + exclusion_reasons).lower()
    cleaned: List[str] = []
    for msg in positives:
        lower = msg.lower()
        if decision != "TRADEABLE" and "live r:r" in lower:
            continue
        if decision != "TRADEABLE" and (
            "entry liegt nahe" in lower
            or "close stark" in lower
            or "relative volumenbestaetigung" in lower
            or "breakout-volumen bestaetigt" in lower
            or "vwap alignment passt" in lower
        ):
            continue
        if vol_confirmed_bool is False and (
            "relative volumenbestaetigung" in lower or "breakout-volumen bestaetigt" in lower
        ):
            continue
        if chase_risk in {"MEDIUM", "HIGH", "CRITICAL"} and (
            "entry liegt nahe" in lower or "nicht gechased" in lower
        ):
            continue
        if fakeout_risk in {"HIGH", "CRITICAL"} and "close stark" in lower and (
            "wick" in risk_text or "close sitzt" in risk_text
        ):
            continue
        if "live r:r" in lower and (
            "live r:r nur" in risk_text or "tp1 bereits erreicht" in risk_text or "tp2 bereits erreicht" in risk_text
        ):
            continue
        cleaned.append(msg)
    return list(dict.fromkeys(cleaned))[:6]


def calculate_trade_health(
    row: Dict[str, Any],
    scanner_name: str = "scanner",
    market_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Return a normalized execution-quality payload for any scanner row."""
    row = row or {}
    warnings: List[str] = []
    positives: List[str] = []
    exclusion_reasons: List[str] = []
    tactical_reasons: List[str] = []

    current = _to_float(_first(row, ["current_price", "price", "Preis", "Price", "close", "Close"]))
    entry = _to_float(_first(row, ["entry", "Entry", "live_entry", "trigger_entry"]))
    stop = _to_float(_first(row, ["stop_loss", "StopLoss", "stop", "Stop", "invalidation_stop"]))
    tp1 = _to_float(_first(row, ["tp1", "TP1", "target1", "target", "Target"]))
    tp2 = _to_float(_first(row, ["tp2", "TP2", "target2"]))
    direction = _direction(row, entry, stop)
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    levels_complete = all(value is not None for value in (entry, stop, tp1, tp2))
    geometry_valid = bool(geometry.get("valid"))
    risk = geometry.get("risk") if geometry_valid else _risk(entry, stop, direction)
    planned_rr = _to_float(_first(row, ["risk_reward", "RiskReward", "rr_effective", "rr_ratio", "rr1"]))
    atr_value = _to_float(_first(row, ["atr", "ATR", "atr_14", "atr14"]))
    spread_pct = _to_float(_first(row, ["spread_pct", "spread_percent"]))
    execution_cost_pct = _execution_cost_pct(row, spread_pct)
    distance_r = _distance_to_entry_r(row, current, entry, risk, direction)
    live_rr_gross = _live_rr(row, current, entry, stop, tp1, tp2, direction)
    live_rr_net = _net_live_rr(
        current,
        entry,
        stop,
        tp1,
        tp2,
        direction,
        execution_cost_pct,
    )
    live_rr = live_rr_net if live_rr_net is not None else live_rr_gross
    tp1_missed, tp2_missed = _target_missed(row, current, direction, tp1, tp2)

    if levels_complete and not geometry_valid:
        exclusion_reasons.append("invalid_trade_geometry")
        warnings.append("Entry/Stop/TP-Geometrie ist ungueltig - Setup nicht handeln")
    elif entry is not None and stop is not None and risk is None:
        exclusion_reasons.append("invalid_stop_geometry")
        warnings.append("Stop liegt auf der falschen Seite des Entries - Setup nicht handeln")

    min_stop_distance: Optional[float] = None
    if entry is not None and entry > 0 and risk is not None and risk > 0:
        spread_distance = entry * max(0.0, spread_pct or 0.0) / 100.0
        atr_distance = max(0.0, atr_value or 0.0) * 0.25
        min_stop_distance = max(entry * 0.001, spread_distance, atr_distance)
        if risk < min_stop_distance:
            exclusion_reasons.append("stop_distance_below_noise_floor")
            warnings.append(
                f"Stop-Abstand {risk:.6g} liegt unter Markt-/ATR-Rauschen {min_stop_distance:.6g}"
            )

    # ── S-2 Audit-Fix: Stop-Breach-Erkennung ──
    # LONG: current <= stop (exakt am Stop zaehlt als Breach) -> Setup invalidiert.
    # SHORT: current >= stop analog. Preis ZWISCHEN Stop und Entry ist ein
    # normaler Pullback und KEIN Breach.
    stop_breached = False
    if current is not None and stop is not None:
        if direction == "LONG" and current <= stop:
            stop_breached = True
        elif direction == "SHORT" and current >= stop:
            stop_breached = True
    if stop_breached:
        exclusion_reasons.append("setup_invalidated_stop_breached")
        warnings.append(
            f"Stop bereits gerissen ({'Preis <= Stop' if direction == 'LONG' else 'Preis >= Stop'}: "
            f"{current} vs {stop}) - Setup invalidiert"
        )

    vol_confirmed_bool: Optional[bool] = None
    vwap_aligned_bool: Optional[bool] = None
    close_strength = False
    near_entry_distance = False

    entry_score = 100
    fakeout_score = 100
    liquidity_score = 100

    raw_entry_quality = str(_first(row, ["entry_quality"], "")).upper()
    if raw_entry_quality == "CHASE":
        entry_score = min(entry_score, 45)
        warnings.append("Backend markiert Entry bereits als CHASE")
    elif raw_entry_quality == "LATE":
        entry_score = min(entry_score, 62)
        warnings.append("Entry ist spaet; Retest bevorzugen")
    elif raw_entry_quality == "EXTENDED":
        entry_score = min(entry_score, 76)
        warnings.append("Entry ist erweitert; nicht hinterherlaufen")
    elif raw_entry_quality == "GOOD":
        positives.append("Entry liegt nahe am Trigger")

    if tp2_missed:
        entry_score -= 55
        tactical_reasons.append("TP2 bereits erreicht - kein frischer Entry")
    elif tp1_missed:
        entry_score -= 38
        warnings.append("TP1 bereits erreicht - Chase/FOMO-Risiko")
        tactical_reasons.append("TP1 bereits erreicht - nur Continuation/Retest handeln")

    if distance_r is not None:
        if distance_r >= 1.0:
            entry_score -= 38
            tactical_reasons.append(f"Preis ist {distance_r:.2f}R vom Entry entfernt")
        elif distance_r >= 0.75:
            entry_score -= 28
            warnings.append(f"Preis ist {distance_r:.2f}R vom Entry entfernt")
        elif distance_r >= 0.35:
            entry_score -= 15
            warnings.append(f"Entry nicht mehr perfekt: {distance_r:.2f}R Entfernung")
        elif 0 <= distance_r <= 0.15:
            # S-2 Audit-Fix: Positivum nur bei nicht-negativer Distanz.
            near_entry_distance = True
        elif distance_r < 0:
            # Negative Distanz = Preis liegt Richtung Stop unter/ueber dem Entry.
            # Das ist ein Pullback (kein Breach solange Stop haelt), aber kein
            # "Entry nahe am Trigger"-Positivum.
            warnings.append(
                f"Preis liegt {abs(distance_r):.2f}R unter Entry Richtung Stop - Retest abwarten"
            )

    if live_rr is not None:
        if live_rr > 30:
            entry_score -= 25
            exclusion_reasons.append("implausible_live_rr")
            warnings.append(f"Live R:R {live_rr:.1f} ist geometrisch unplausibel - Levels pruefen")
        elif live_rr > 15:
            entry_score -= 12
            warnings.append(f"Live R:R {live_rr:.1f} ungewoehnlich hoch - Stop/Targets pruefen")
        elif live_rr < HARD_MIN_RR:
            entry_score -= 32
            tactical_reasons.append(f"Live R:R nur {live_rr:.2f}")
        elif live_rr < PREFERRED_MIN_RR:
            entry_score -= 16
            warnings.append(f"Live R:R {live_rr:.2f} unter Wunschbereich")
        else:
            rr_label = "Netto-R:R" if live_rr_net is not None else "Live R:R"
            positives.append(f"{rr_label} {live_rr:.2f} akzeptabel")

    rvol = _to_float(_first(row, ["rvol", "RVOL", "relative_volume"]))
    if rvol is not None:
        if rvol < 0.5:
            fakeout_score -= 28
            warnings.append("RVOL sehr niedrig - Ausbruch kann austrocknen")
        elif rvol < MIN_RVOL:
            fakeout_score -= 20
            warnings.append("RVOL unter Mindestbereich")
        elif rvol < 1.0:
            fakeout_score -= 10
            warnings.append("RVOL noch unter 1.0")
        elif rvol >= 2.0:
            positives.append("Starke relative Volumenbestaetigung")

    vol_confirmed_raw = _first(row, ["vol_confirmed", "volume_confirmed", "breakout_confirmed"])
    if vol_confirmed_raw is not None:
        vol_confirmed_bool = _to_bool(vol_confirmed_raw)
        if vol_confirmed_bool:
            positives.append("Breakout-Volumen bestaetigt")
        else:
            fakeout_score -= 25
            warnings.append("Breakout-Volumen nicht bestaetigt")

    vwap_aligned_raw = _first(row, ["vwap_aligned", "above_vwap", "vwap_ok"])
    if vwap_aligned_raw is not None:
        vwap_aligned_bool = _to_bool(vwap_aligned_raw)
        if vwap_aligned_bool:
            positives.append("VWAP alignment passt")
        else:
            fakeout_score -= 16
            warnings.append("VWAP alignment fehlt")

    close_pos = _to_float(_first(row, ["close_pos", "close_position", "closePosition"]))
    if close_pos is not None:
        if direction == "LONG":
            if close_pos < 0.45:
                fakeout_score -= 24
                warnings.append("Close sitzt tief in der Kerze - Upper-Wick/Fakeout-Risiko")
            elif close_pos < 0.60:
                fakeout_score -= 12
                warnings.append("Close nicht stark nahe High")
            elif close_pos >= 0.75:
                close_strength = True
                positives.append("Close stark nahe High")
        else:
            if close_pos > 0.55:
                fakeout_score -= 24
                warnings.append("Short-Close sitzt hoch in der Kerze - Squeeze-Risiko")
            elif close_pos > 0.40:
                fakeout_score -= 12
                warnings.append("Short-Close nicht stark nahe Low")
            elif close_pos <= 0.25:
                close_strength = True
                positives.append("Close stark nahe Low")

    upper_wick_pct = _to_float(_first(row, ["upper_wick_pct", "UpperWickPct"]))
    lower_wick_pct = _to_float(_first(row, ["lower_wick_pct", "LowerWickPct"]))
    if direction == "LONG" and upper_wick_pct is not None and upper_wick_pct >= 35:
        fakeout_score -= 18
        warnings.append("Grosser Upper Wick - Long-Fakeout-Risiko")
    if direction == "SHORT" and lower_wick_pct is not None and lower_wick_pct >= 35:
        fakeout_score -= 18
        warnings.append("Grosser Lower Wick - Short-Fakeout-Risiko")

    breakout_state = str(_first(row, ["breakout_state", "or_phase"], "")).lower()
    if scanner_name.startswith("orb"):
        if breakout_state and breakout_state not in {"active_breakout", "prime", "active"}:
            fakeout_score -= 18
            warnings.append(f"ORB Zustand ist {breakout_state} statt aktivem Breakout")
        if _to_bool(_first(row, ["late_session", "is_late_orb_session"], False)):
            entry_score -= 10
            warnings.append("Spaetes ORB-Fenster - Edge reduziert, aber kein Fakeout-Signal")
        recent_hold_pct = _to_float(_first(row, ["recent_hold_pct", "orb_recent_hold_pct"]))
        if recent_hold_pct is not None:
            if recent_hold_pct < 0.34:
                fakeout_score -= 18
                warnings.append("ORB Recent-Hold schwach - Breakout haelt nicht sauber")
            elif recent_hold_pct >= 0.67:
                positives.append("ORB Recent-Hold bestaetigt")
        hold_pct = _to_float(_first(row, ["hold_pct", "orb_hold_pct"]))
        if hold_pct is not None and hold_pct < 0.25:
            fakeout_score -= 10
            warnings.append("ORB Hold-Anteil schwach")

    partial_data = _to_bool(_first(row, ["partial_data"], False))
    data_warning = _first(row, ["data_warning", "warning", "Warnings"])
    if partial_data or data_warning:
        fakeout_score -= 12
        warnings.append(str(data_warning or "Datenbasis ist unvollstaendig"))

    risk_flag = str(_first(row, ["risk_flag", "Risk_Flag"], "")).lower()
    negative_flags = _as_list(_first(row, ["negative_flags", "Negative_Flags"]))
    danger_words = ("offering", "dilution", "reverse split", "bankruptcy", "halt", "delist", "complete response")
    if risk_flag in {"high", "red", "critical"}:
        fakeout_score -= 26
        warnings.append("Biotech Risk-Flag ist hoch")
    if any(any(word in flag.lower() for word in danger_words) for flag in negative_flags):
        fakeout_score -= 30
        exclusion_reasons.append("Negative News-/Dilution-Flags erkannt")
    elif negative_flags:
        fakeout_score -= 10
        warnings.append("Negative Flags vorhanden")

    dollar_volume = _to_float(_first(row, ["dollar_volume", "dollar_vol", "DollarVolume"]))
    volume = _to_float(_first(row, ["volume", "Volumen"]))
    price = current or entry
    if spread_pct is not None:
        max_spread = 2.5 if "crypto" in scanner_name or "listing" in scanner_name else 2.0
        if spread_pct > max_spread:
            liquidity_score -= 35
            exclusion_reasons.append(f"Spread {spread_pct:.2f}% zu breit")
        elif spread_pct > max_spread * 0.5:
            liquidity_score -= 16
            warnings.append(f"Spread {spread_pct:.2f}% erhoeht")
    if dollar_volume is not None:
        if dollar_volume < 1_000_000:
            liquidity_score -= 30
            warnings.append("Dollar-Volumen unter 1 Mio. - Slippage-Risiko")
        elif dollar_volume < 5_000_000:
            liquidity_score -= 12
            warnings.append("Dollar-Volumen unter 5 Mio.")
    elif volume is not None and price is not None:
        dv = volume * price
        if dv < 1_000_000:
            liquidity_score -= 22
            warnings.append("Geschaetztes Dollar-Volumen unter 1 Mio.")
    if price is not None and price < 1:
        liquidity_score -= 8
        warnings.append("Sub-Dollar-Ticker - Manipulations-/Spread-Risiko")

    has_levels = geometry_valid
    if not has_levels:
        entry_score = min(entry_score, 78)
        if not levels_complete:
            warnings.append("Keine vollstaendigen Entry/Stop/TP-Level - erst Trigger abwarten")

    context_summary = {}
    if market_context:
        context_summary = market_context.get("summary") or market_context
        regime = str(context_summary.get("regime") or market_context.get("regime") or "NEUTRAL").upper()
        trade_mode = str(context_summary.get("trade_mode") or market_context.get("trade_mode") or "SELECTIVE").upper()
        headline_level = str(context_summary.get("headline_level") or "").upper()
        event_level = str(context_summary.get("event_level") or "").upper()
        context_penalty = 0
        if trade_mode == "PROTECT_CAPITAL" or regime == "PANIC":
            context_penalty = 22
            warnings.append("Market Weather PANIC: Kapital schuetzen, nur absolute A+ Retests")
        elif trade_mode == "DEFENSIVE" or regime == "RISK_OFF":
            context_penalty = 13
            warnings.append("Market Weather Risk-Off: Longs nur defensiv/Retest, keine FOMO Entries")
        elif trade_mode == "CAUTIOUS" or regime == "RISK_OFF_LIGHT":
            context_penalty = 8
            warnings.append("Market Weather Risk-Off-Light: selektiv bleiben, keine News-FOMO Entries")
        elif trade_mode == "SELECTIVE" or regime == "NEUTRAL":
            context_penalty = 5
        if headline_level in {"HIGH", "EXTREME"}:
            context_penalty += 8
            warnings.append("Headline-Risiko hoch - politische News koennen Kerzen drehen")
        elif headline_level in {"UNKNOWN", "STALE"}:
            context_penalty += 5
            warnings.append("Headline-Risiko unbekannt - defensiver handeln")
        if event_level in {"HIGH", "EXTREME"}:
            context_penalty += 8
            warnings.append("Event-Risiko hoch - Makro/FED-Spike moeglich")

        if context_penalty:
            if direction == "LONG":
                entry_score -= context_penalty
            elif regime in {"RISK_OFF_LIGHT", "RISK_OFF", "PANIC"}:
                entry_score -= max(2, int(context_penalty * 0.35))
                positives.append("Risk-Off kann Short-Setups unterstuetzen, aber trotzdem nicht chasen")
            else:
                entry_score -= max(2, int(context_penalty * 0.5))

    entry_score = max(0, min(100, int(round(entry_score))))
    fakeout_score = max(0, min(100, int(round(fakeout_score))))
    liquidity_score = max(0, min(100, int(round(liquidity_score))))

    # S-2 Audit-Fix: Gerissener Stop erzwingt CRITICAL-Scores, egal wie gut
    # die restlichen Komponenten aussehen.
    if stop_breached:
        entry_score = min(entry_score, 15)
        fakeout_score = min(fakeout_score, 15)
        near_entry_distance = False
        live_rr = 0.0
        live_rr_gross = 0.0
        if live_rr_net is not None:
            live_rr_net = 0.0

    if near_entry_distance:
        if entry_score >= 70 and not tactical_reasons and raw_entry_quality not in {"CHASE", "LATE", "EXTENDED"}:
            positives.append("Entry liegt nahe am Trigger")
        elif entry_score < 70:
            warnings.append("Preis ist nah am Entry, aber Chase-Risiko bleibt durch Setup-/Market-Filter erhoeht")

    health_score = int(round(entry_score * 0.45 + fakeout_score * 0.35 + liquidity_score * 0.20))
    if stop_breached:
        health_score = min(health_score, 15)

    chase_risk = _risk_band(entry_score)
    fakeout_risk = _risk_band(fakeout_score)
    liquidity_risk = _risk_band(liquidity_score)

    strong_continuation = bool(
        tactical_reasons
        and not tp2_missed
        and close_strength
        and (rvol is not None and rvol >= 2.0)
        and vol_confirmed_bool is not False
        and vwap_aligned_bool is not False
        and fakeout_score >= 70
        and liquidity_score >= 65
    )
    if strong_continuation:
        positives.append("Starke Momentum-Fortsetzung: nicht market chasen, Retest/Flag/VWAP-Hold abwarten")

    if vol_confirmed_bool is False and rvol is not None and rvol >= 2.0:
        warnings.append(f"RVOL {rvol:.1f}x hoch, aber der konkrete Breakout-Bar ist nicht bestaetigt")

    if exclusion_reasons:
        decision = "NO_TRADE"
    elif strong_continuation:
        decision = "WAIT_FOR_CONTINUATION"
    elif tactical_reasons or health_score < 50 or chase_risk == "CRITICAL" or fakeout_risk == "CRITICAL":
        decision = "NO_TRADE"
    elif not has_levels and health_score >= 65:
        decision = "WAIT_FOR_TRIGGER"
    elif health_score >= 80 and chase_risk == "LOW" and fakeout_risk == "LOW" and liquidity_risk != "CRITICAL":
        decision = "TRADEABLE"
    elif health_score >= 65:
        decision = "WAIT_FOR_RETEST"
    elif health_score >= 50:
        decision = "WATCH_ONLY"
    else:
        decision = "NO_TRADE"

    positives = _sanitize_trade_health_messages(
        positives,
        warnings,
        tactical_reasons,
        exclusion_reasons,
        decision=decision,
        vol_confirmed_bool=vol_confirmed_bool,
        chase_risk=chase_risk,
        fakeout_risk=fakeout_risk,
    )

    return {
        "scanner": scanner_name,
        "health_score": health_score,
        "decision": decision,
        "decision_label": _decision_label(decision),
        "risk_level": "CRITICAL" if stop_breached else _risk_band(health_score),
        "stop_breached": stop_breached,
        "entry_quality": _entry_quality(entry_score),
        "entry_quality_score": entry_score,
        "fakeout_risk": fakeout_risk,
        "fakeout_risk_score": fakeout_score,
        "chase_risk": chase_risk,
        "chase_risk_score": entry_score,
        "liquidity_risk": liquidity_risk,
        "liquidity_score": liquidity_score,
        "direction": direction,
        "trade_geometry_valid": geometry_valid,
        "trade_geometry_errors": geometry.get("errors", []),
        "warnings": list(dict.fromkeys(warnings + tactical_reasons))[:8],
        "positives": positives,
        "exclusion_reasons": list(dict.fromkeys(exclusion_reasons))[:6],
        "tactical_reasons": list(dict.fromkeys(tactical_reasons))[:6],
        "continuation_watch": strong_continuation,
        "market_context": {
            "regime": context_summary.get("regime"),
            "trade_mode": context_summary.get("trade_mode"),
            "overall_risk_score": context_summary.get("overall_risk_score"),
            "size_multiplier": context_summary.get("size_multiplier"),
            "headline_level": context_summary.get("headline_level"),
            "event_level": context_summary.get("event_level"),
        } if context_summary else None,
        "metrics": {
            "current_price": current,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
            "risk": risk,
            "distance_to_entry_r": distance_r,
            "live_rr": live_rr,
            "live_rr_gross": live_rr_gross,
            "live_rr_net": live_rr_net,
            "execution_cost_pct": execution_cost_pct,
            "rr_cost_basis": "net" if live_rr_net is not None else "gross_no_cost_data",
            "planned_rr": planned_rr,
            "min_stop_distance": round(min_stop_distance, 8) if min_stop_distance is not None else None,
            "rvol": rvol,
            "close_pos": close_pos,
            "spread_pct": spread_pct,
            "dollar_volume": dollar_volume,
        },
        "policy_note": "Execution guardrail; not a profit guarantee.",
    }
