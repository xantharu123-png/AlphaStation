"""Presentation only: candidates never acquire execution or mail permission here."""
from __future__ import annotations

import math
from copy import deepcopy


LABELS = {
    "crossed_resistance_unconfirmed": "Widerstandsausbruch noch nicht durch Schlusskurs bestaetigt",
    "crossed_support_unconfirmed": "Unterstuetzungsbruch noch nicht durch Schlusskurs bestaetigt",
    "first_opposing_barrier_before_minimum_rr": "Naechste Gegenbarriere sehr nahe; weniger als 1,35R Platz",
    "near_structural_barrier": "Naechste Unterstuetzung / naechster Widerstand sehr nahe",
    "breakout_confirmed_retest_pending": "Ausbruch bestaetigt; Ruecktest noch offen",
    "invalid_trade_geometry": "Kein gueltiger Handelsplan: Entry, Stop oder Ziele passen nicht zusammen",
    "no_structural_invalidation": "Kein belastbarer struktureller Stop vorhanden",
    "causal_structure_missing": "Bestaetigte Marktstruktur fehlt fuer den Handelsplan",
    "causal_structure_unavailable": "Bestaetigte Marktstruktur fehlt fuer den Handelsplan",
    "plan_unavailable": "Kein freigegebener Handelsplan vorhanden",
    "native_trade_levels_missing": "Kein nativer, freigegebener Handelsplan vorhanden",
    "trade_health_no_trade": "Risiko-/Handelsplanpruefung nicht freigegeben",
    "WAIT_FOR_RETEST": "Ruecktest noch nicht bestaetigt",
    "WAIT_FOR_TRIGGER": "Ausbruch / Einstiegstrigger noch nicht bestaetigt",
    "WAIT_FOR_CONTINUATION": "Fortsetzung noch nicht bestaetigt",
    "not_released": "Setup vorhanden; noch kein freigegebenes Handelssignal",
    "trigger_not_current": "Einstiegstrigger nicht mehr aktuell; neue Bestaetigung abwarten",
    "market_data_invalid": "Marktdaten ungueltig oder nicht aktuell",
    "display_metadata_invalid": "Freigabedetails nicht verlaesslich lesbar",
}

UNUSABLE = frozenset({
    "invalid_bar_value", "scan_data_invalid", "scan_data_incomplete",
    "invalid_price", "invalid_symbol_or_missing_prev_close",
    "market_data_unavailable", "market_data_invalid", "stale_market_data",
    "stock_swing_data_invalid", "stock_swing_reference_invalid",
    "stock_swing_reference_stale", "non_common_stock_product",
    "invalid_ohlcv", "invalid_ohlc", "invalid_ohlcv_data", "invalid_ohlcv_bar",
    "invalid_ohlcv_bars", "partial_crypto_data", "price_stale",
    "stock_swing_price_missing", "stock_swing_reference_missing",
})

TRIGGER_NOT_CURRENT = frozenset({
    "closed_5m_data_stale", "closed_5m_data_missing", "stale_5m_candle",
    "new_listing_short_cache_contract_invalid", "trigger_not_current",
})

PLAN_BLOCKERS = frozenset({
    "invalid_trade_geometry", "invalid_trade_plan", "estimated_trade_plan",
    "native_trade_levels_missing", "no_structural_invalidation", "plan_unavailable",
    "causal_structure_missing", "causal_structure_unavailable",
    "crossed_resistance_unconfirmed", "crossed_support_unconfirmed",
    "first_opposing_barrier_before_minimum_rr", "near_structural_barrier",
    "near_structural_barrier_wait_trigger", "trade_health_no_trade",
})


def mapping(value):
    return value if isinstance(value, dict) else {}


def strings(value):
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str) and item]
    return []


def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) else None
    except (TypeError, ValueError, OverflowError):
        return None


def mail_check_summary(row, state, *, reasons, labels, minimum_score, assessed_at, complete=True):
    """Explain existing decisions; never authorize a send or recompute scores."""
    row, state = mapping(row), mapping(state)
    score = number(state.get("score"))
    minimum = number(minimum_score)
    # Match the producer score precedence; raw_score may predate a legitimate
    # scanner cap and is not the currently classified setup score.
    setup_score = next((value for key in ("BI_Score", "Score", "score", "setup_score", "raw_score")
                        if (value := number(row.get(key))) is not None), None)
    codes = list(dict.fromkeys(strings(reasons)))
    translated = {**mapping(labels), **LABELS}
    details = []
    for code in codes:
        label = translated.get(code, code.replace("_", " "))
        if code == "score_below_alert_threshold" and score is not None and minimum is not None:
            label = f"Mail-/Handelsplan-Score {score:g}; benoetigt mindestens {minimum:g}"
        details.append({"code": code, "label": label})
    valid = bool(complete and score is not None and 0 <= score <= 100
                 and minimum is not None and 0 <= minimum <= 100
                 and (score >= minimum or codes))
    return {
        "schema_version": 1,
        "status": "blocked" if valid and codes else "checks_passed" if valid else "unavailable",
        "semantics": "read_only_precheck_not_delivery_or_send_permission",
        "assessed_at": number(assessed_at),
        "setup_score": setup_score,
        "trade_score": score,
        "minimum_trade_score": minimum,
        "reasons": details,
    }


def reasons(row):
    row = mapping(row)
    result = []
    for source in (row, row.get("trade_health"), row.get("_quality")):
        if not isinstance(source, dict):
            continue
        for key in ("scanner_suppression_reasons", "exclusion_reasons", "tactical_reasons", "orb_gate_reasons", "risk_flags"):
            result.extend(strings(source.get(key)))
    native_reason = row.get("native_plan_reason") or mapping(row.get("native_plan_diagnostics")).get("reason")
    if native_reason and native_reason != "native_structure_plan":
        result.append(str(native_reason))
    if row.get("barrier_gate_active"):
        result.append("near_structural_barrier")
    if row.get("retest_status") == "not_confirmed" or mapping(row.get("trade_setup")).get("retest_status") == "not_confirmed":
        result.append("breakout_confirmed_retest_pending")
    result.extend(strings(row.get("trigger_expiry_reason")))
    if row.get("trigger_expired") is True:
        result.append("trigger_not_current")
    return list(dict.fromkeys(result))


def unusable_reason(row):
    """Explicit bad data stays excluded; a missing trade plan is NOT bad OHLC."""
    if not isinstance(row, dict):
        return "invalid_row"
    for source in (row, mapping(row.get("_quality"))):
        for key in ("data_valid", "market_data_valid", "ohlcv_valid"):
            if source.get(key) is False:
                return "market_data_invalid"
        for key in ("data_error", "data_error_code", "data_status"):
            value = str(source.get(key) or "").strip().lower()
            if value in UNUSABLE or value in {"invalid", "error", "stale", "unavailable"}:
                return "market_data_invalid"
    return next((reason for reason in reasons(row) if reason.strip().lower() in UNUSABLE), None)


def present(row, *, released=False, context=False, labels=None):
    """Copy row and annotate view metadata; never change canonical trade fields."""
    row = mapping(row)
    item = deepcopy(row)
    codes = reasons(row)
    health = mapping(row.get("trade_health"))
    setup = mapping(row.get("trade_setup"))
    diagnostics = mapping(row.get("native_plan_diagnostics"))
    quality = mapping(row.get("_quality"))
    decision = str(row.get("trade_decision") or health.get("decision") or "").upper()
    action = str(row.get("trade_action") or row.get("action") or "").upper()
    signal = str(row.get("trade_signal") or row.get("signal") or row.get("Signal") or "").upper()
    states = " ".join((action, signal, decision, str(row.get("entry_status") or "").upper(),
                       str(row.get("scanner_decision") or "").upper(),
                       str(row.get("signal_quality") or "").upper()))
    waiting = any(token in states for token in (
        "WAIT", "WARTEN", "WATCH", "ARMED", "NICHT", "NOT_RELEASED", "NO_TRADE", "BLOCKED", "BEOBACHTEN",
    ))
    stale_trigger = any(
        code in TRIGGER_NOT_CURRENT or "trigger" in code.lower() and any(
            token in code.lower() for token in ("stale", "expired", "missing", "invalid"))
        for code in codes
    )
    if stale_trigger and "trigger_not_current" not in codes:
        codes.append("trigger_not_current")
    invalid_metadata = any(row.get(key) is not None and not isinstance(row[key], dict)
                           for key in ("trade_health", "trade_setup", "_quality", "native_plan_diagnostics"))
    if invalid_metadata:
        codes.append("display_metadata_invalid")
    data_problem = unusable_reason(row)
    if data_problem and data_problem not in codes:
        codes.append(data_problem)
    blocked_plan = (str(row.get("native_plan_status") or "").lower() in {"unavailable", "invalid", "pending", "failed"}
                    or bool(row.get("barrier_gate_active")) or bool(PLAN_BLOCKERS.intersection(codes)))
    released = bool(released and not waiting and not blocked_plan and not stale_trigger
                    and not invalid_metadata and not data_problem)
    if not released and not context and not codes:
        codes.append(action if action in LABELS else decision if decision in LABELS else "not_released")
    translated = {**mapping(labels), **LABELS}
    warnings = [{"code": code, "label": translated.get(code, code.replace("_", " "))} for code in codes]
    for value in strings(quality.get("warnings")):
        if isinstance(value, str) and value and not any(w["label"] == value for w in warnings):
            warnings.append({"code": "risk_warning", "label": value})
    # Only report supplied, finite distances. Never fabricate a risk or R:R.
    barrier = next((value for value in (
        row.get("nearest_barrier"), row.get("overhead_resistance"), row.get("underlying_support"),
        setup.get("nearest_barrier"), diagnostics.get("barrier"),
    ) if isinstance(value, dict)), None)
    if isinstance(barrier, dict):
        price = number(barrier.get("price"))
        current = next((n for key in ("price", "current_price", "Price", "Preis", "current", "Kurs", "Entry", "entry") if (n := number(row.get(key))) is not None and n > 0), None)
        for warning in warnings:
            if warning["code"] not in {"near_structural_barrier", "first_opposing_barrier_before_minimum_rr", "crossed_resistance_unconfirmed", "crossed_support_unconfirmed"}:
                continue
            if price is not None and price > 0:
                warning["price"] = price
                if current:
                    warning["distance_pct"] = round(abs(price - current) / current * 100, 4)
            distance_r = number(barrier.get("distance_r"))
            if distance_r is not None and distance_r >= 0:
                warning["distance_r"] = distance_r
            if isinstance(barrier.get("timeframe"), str):
                warning["timeframe"] = barrier["timeframe"]
            warning["side"] = str(barrier.get("side") or "")
    item.update(
        visibility_status="context" if context else "released" if released else "candidate_warning",
        visibility_label="Kontext / Positionsverwaltung" if context else "Freigegebenes Signal" if released else "Kandidat mit Warnung · kein freigegebenes Signal",
        visibility_warnings=warnings,
        visibility_is_trade_signal=bool(released and not context),
    )
    quality = dict(mapping(item.get("_quality")))
    quality.update(signal_only=False, display_mode="candidates_with_warnings")
    item["_quality"] = quality
    return item


def counts(rows):
    result = {"released": 0, "candidate_warning": 0, "context": 0, "total": 0, "warning_count": 0}
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = row.get("visibility_status")
        if state in {"released", "candidate_warning", "context"}:
            result[state] += 1
            result["total"] += 1
            result["warning_count"] += bool(row.get("visibility_warnings"))
    return result
