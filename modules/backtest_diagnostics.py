"""Reporting-only BI funnel validation; never changes scanner eligibility."""

from copy import deepcopy


BI_BACKTEST_IDS = {"scanner_bi_long", "scanner_bi_short", "bi_long", "bi_short"}
_UNSET = object()
_COVERAGE_COUNTS = (
    "selected_tickers", "tickers_with_test_period_data", "tickers_with_usable_windows",
    "expected_fetch_sessions", "loaded_fetch_sessions", "selected_expected_sessions",
    "selected_observed_sessions",
)
_FUNNEL_COUNTS = (
    "windows_considered", "occupied_windows", "invalid_or_incomplete_windows",
    "price_filtered_windows", "volume_filtered_windows", "indicator_evaluated_windows",
    "indicator_qualified_candidates", "indicator_contract_unavailable_windows",
    "indicator_rejected_windows", "plan_rejected_candidates", "accepted_plans",
    "filled_trades", "no_fill", "unresolved",
)


def _count(value):
    return type(value) is int and value >= 0


def validate_bi_diagnostics(value, *, total_signals=None, n_tickers=None, trade_rows=_UNSET):
    """Reject contradictory counters rather than turn absent evidence into zero."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None
    coverage, funnel = value.get("coverage"), value.get("funnel")
    if not isinstance(coverage, dict) or not isinstance(funnel, dict):
        return None
    if any(not _count(coverage.get(key)) for key in _COVERAGE_COUNTS):
        return None
    if any(not _count(funnel.get(key)) for key in _FUNNEL_COUNTS):
        return None
    for key in ("confluence_histogram", "hard_gate_counts", "unavailable_factor_counts", "plan_rejection_counts"):
        counts = funnel.get(key)
        if not isinstance(counts, dict) or any(not isinstance(k, str) or not _count(v) for k, v in counts.items()):
            return None
    for key in ("empty_fetch_dates", "failed_fetch_dates"):
        dates = coverage.get(key)
        if not isinstance(dates, list) or any(not isinstance(day, str) for day in dates):
            return None
    for key in ("missing_sessions_by_ticker", "invalid_bars_by_ticker"):
        if not isinstance(coverage.get(key), dict):
            return None
    if any(not isinstance(ticker, str) or not isinstance(days, list)
           or any(not isinstance(day, str) for day in days)
           for ticker, days in coverage["missing_sessions_by_ticker"].items()):
        return None
    if any(not isinstance(ticker, str) or not _count(count)
           for ticker, count in coverage["invalid_bars_by_ticker"].items()):
        return None
    if not (coverage["selected_tickers"] >= coverage["tickers_with_test_period_data"] >= coverage["tickers_with_usable_windows"]):
        return None
    if (coverage["loaded_fetch_sessions"] > coverage["expected_fetch_sessions"]
            or coverage["selected_observed_sessions"] > coverage["selected_expected_sessions"]):
        return None
    if funnel["windows_considered"] != sum(funnel[key] for key in (
        "occupied_windows", "invalid_or_incomplete_windows", "price_filtered_windows",
        "volume_filtered_windows", "indicator_evaluated_windows",
    )):
        return None
    if funnel["indicator_evaluated_windows"] != sum(funnel[key] for key in (
        "indicator_contract_unavailable_windows", "indicator_rejected_windows", "indicator_qualified_candidates",
    )):
        return None
    if funnel["indicator_qualified_candidates"] != funnel["plan_rejected_candidates"] + funnel["accepted_plans"]:
        return None
    if sum(funnel["plan_rejection_counts"].values()) != funnel["plan_rejected_candidates"]:
        return None
    if sum(funnel["confluence_histogram"].values()) != funnel["indicator_evaluated_windows"]:
        return None
    if any(funnel[key] > funnel["accepted_plans"] for key in ("filled_trades", "no_fill", "unresolved")):
        return None
    # Cached tables omit unresolved rows and cap decided rows at 150, so the
    # aggregate partition must also hold without access to the producer rows.
    # Unresolved can overlap filled, but never definitive NO_FILL. Every
    # accepted plan without a fill or definitive NO_FILL must be unresolved.
    accepted, filled, no_fill, unresolved = (
        funnel[key] for key in ("accepted_plans", "filled_trades", "no_fill", "unresolved")
    )
    if (filled + no_fill > accepted
            or accepted - filled - no_fill > unresolved
            or no_fill + unresolved > accepted):
        return None
    if total_signals is not None and (not _count(total_signals) or total_signals != funnel["accepted_plans"]):
        return None
    if n_tickers is not None and (not _count(n_tickers) or n_tickers != coverage["selected_tickers"]):
        return None
    if trade_rows is not _UNSET:
        # Only the complete producer rows may be checked here. The rendered
        # report contains decided rows only and caps its table at 150 entries.
        if not isinstance(trade_rows, list) or len(trade_rows) != funnel["accepted_plans"]:
            return None
        for row in trade_rows:
            if (not isinstance(row, dict) or type(row.get("entry_filled")) is not bool
                    or not isinstance(row.get("outcome"), str) or not row["outcome"].strip()):
                return None
            outcome = row["outcome"].upper()
            if (outcome == "NO_FILL" and row["entry_filled"]
                    or outcome not in {"NO_FILL", "UNRESOLVED"} and not row["entry_filled"]):
                return None
        actual_counts = {
            "filled_trades": sum(row["entry_filled"] for row in trade_rows),
            "no_fill": sum(row["outcome"].upper() == "NO_FILL" for row in trade_rows),
            "unresolved": sum(row["outcome"].upper() == "UNRESOLVED" for row in trade_rows),
        }
        if any(funnel[key] != count for key, count in actual_counts.items()):
            return None
    return deepcopy(value)


def _zero_stage(diagnostics, data_quality):
    coverage, funnel = diagnostics["coverage"], diagnostics["funnel"]
    quality_status = str((data_quality or {}).get("status") or "").upper()
    if (quality_status == "UNAVAILABLE"
            or (quality_status == "PARTIAL" and (data_quality or {}).get("followup_pending_only") is not True)
            or coverage["failed_fetch_dates"] or coverage["empty_fetch_dates"]
            or any(coverage["missing_sessions_by_ticker"].values())
            or any(coverage["invalid_bars_by_ticker"].values())
            or coverage["loaded_fetch_sessions"] < coverage["expected_fetch_sessions"]
            or coverage["selected_observed_sessions"] < coverage["selected_expected_sessions"]):
        return "data_incomplete"
    if not coverage["selected_tickers"]:
        return "no_selected_tickers"
    if not coverage["tickers_with_usable_windows"]:
        return "no_usable_windows"
    if not funnel["indicator_evaluated_windows"]:
        return "prefilter_rejected"
    if not funnel["indicator_qualified_candidates"]:
        if funnel["indicator_contract_unavailable_windows"]:
            return "indicator_unavailable"
        return "indicator_rejected"
    if not funnel["accepted_plans"]:
        return "plan_rejected"
    if not funnel["filled_trades"]:
        if funnel["unresolved"]:
            return "entry_pending"
        return "no_fills"
    return None


def apply_bi_backtest_diagnosis(result, strategy=None, *, trade_rows=_UNSET, source_quality=None, source_summary=None):
    """Keep old reports, but do not pretend their missing rejection funnel exists."""
    result = deepcopy(result)
    if str(strategy or result.get("strategy") or "") not in BI_BACKTEST_IDS or result.get("data_available") is False:
        return result
    raw = result.get("diagnostics")
    source_counts = source_summary if isinstance(source_summary, dict) else result
    diagnostics = validate_bi_diagnostics(raw, total_signals=source_counts.get("total_signals"),
                                          n_tickers=source_counts.get("n_tickers"), trade_rows=trade_rows)
    result["diagnostics_status"] = "available" if diagnostics is not None else "missing" if raw is None else "invalid"
    if diagnostics is None:
        result.pop("diagnostics", None)
        if raw is not None:
            quality = deepcopy(result.get("data_quality") or {})
            quality["status"] = "PARTIAL"
            quality["limitations"] = list(quality.get("limitations") or []) + ["BI-Prüfzähler sind widersprüchlich oder unvollständig."]
            result["data_quality"] = quality
            result["verdict"] = {
                "status": "data_incomplete", "label": "DIAGNOSE UNVOLLSTÄNDIG", "color": "orange", "tradable": False,
                "summary": "Die BI-Prüfzähler stimmen nicht überein; die Ursache ist nicht belastbar ausgewiesen.", "reasons": [],
            }
            result["out_of_sample"] = {**(result.get("out_of_sample") or {}), "status": "data_incomplete", "robust": False, "diagnostic_only": True}
        if (result.get("total_signals") == 0 and result.get("total_trades") == 0
                and (result.get("verdict") or {}).get("status") != "data_incomplete"):
            result["verdict"] = {
                "status": "diagnosis_unavailable", "label": "URSACHE NICHT GESPEICHERT", "color": "gray", "tradable": False,
                "summary": "Dieser Lauf enthält keine Kandidaten- und Ablehnungsdiagnose. Für die Ursache ist ein neuer Lauf nötig.",
                "reasons": [],
            }
        return result
    if trade_rows is not _UNSET:
        # A complete past history and not-yet-observed future sessions are
        # distinct. Keep performance partial, without inventing a provider gap.
        quality = deepcopy(result.get("data_quality") or {})
        quality.pop("followup_pending_only", None)
        pending = [row for row in trade_rows if row["outcome"].upper() == "UNRESOLVED"]
        coverage = diagnostics["coverage"]
        complete_history = (
            isinstance(source_quality, dict)
            and source_quality.get("status") == "NO_KNOWN_FETCH_OR_SESSION_GAP"
            and not coverage["failed_fetch_dates"] and not coverage["empty_fetch_dates"]
            and not any(coverage["missing_sessions_by_ticker"].values())
            and not any(coverage["invalid_bars_by_ticker"].values())
            and coverage["loaded_fetch_sessions"] == coverage["expected_fetch_sessions"]
            and coverage["selected_observed_sessions"] == coverage["selected_expected_sessions"]
            and not any(quality.get(key) for key in (
                "failed_fetch_days", "failed_fetch_dates", "unavailable_tickers", "missing_expected_sessions",
                "missing_r_decided_trades", "missing_r_upper_decided_trades", "missing_pnl_trades",
            ))
        )
        if (pending and complete_history and all(row.get("evaluation_status") in {
                "INCOMPLETE_ENTRY_WINDOW", "INCOMPLETE_HOLDING_WINDOW"} for row in pending)):
            quality["followup_pending_only"] = True
        result["data_quality"] = quality
    stage = _zero_stage(diagnostics, result.get("data_quality"))
    diagnostics["zero_result_stage"] = stage
    result["diagnostics"] = diagnostics
    if result.get("total_trades", 0) > 0:
        return result
    labels = {
        "data_incomplete": ("data_incomplete", "DATEN UNVOLLSTÄNDIG", "Die verfügbaren Daten erlauben keine vollständige Auswertung.", "orange"),
        "no_selected_tickers": ("no_selected_tickers", "KEINE AKTIEN AUSGEWÄHLT", "Keine Aktie erfüllt die ausgewiesene Universumsauswahl.", "gray"),
        "no_usable_windows": ("no_usable_windows", "KEINE AUSWERTBAREN FENSTER", "Für die ausgewählten Aktien fehlen auswertbare Analysefenster.", "orange"),
        "prefilter_rejected": ("prefilter_rejected", "PREIS-/VOLUMENFILTER", "Kein auswertbares Fenster erfüllt die gewählten Preis- und Volumenfilter.", "gray"),
        "indicator_rejected": ("no_bi_setups", "KEINE 17/20-SETUPS", "Kein geprüftes Fenster erfüllt den vollständigen BI-Indikatorvertrag.", "gray"),
        "indicator_unavailable": ("indicator_unavailable", "BI-PRÜFUNG UNVOLLSTÄNDIG", "Nicht alle Indikatoren waren berechenbar; ein vollständiges negatives BI-Ergebnis ist nicht belegt.", "orange"),
        "plan_rejected": ("plans_rejected", "PLÄNE ABGELEHNT", "BI-Setups erkannt, aber kein Handelsplan freigegeben. Gründe stehen in den Prüfdetails.", "orange"),
        "no_fills": ("entry_not_reached", "KEIN EINSTIEG", "Pläne akzeptiert, aber kein Einstieg im beobachteten Zeitraum belegt.", "gray"),
        "entry_pending": ("entry_pending", "EINSTIEGSPRÜFUNG NOCH OFFEN", "Für mindestens einen akzeptierten Plan fehlen noch die notwendigen Folgekerzen.", "gray"),
        None: ("outcomes_pending", "ERGEBNISSE NOCH OFFEN", "Einstiege vorhanden, aber noch keine abgeschlossenen auswertbaren Trades.", "gray"),
    }
    status, label, summary, color = labels[stage]
    result["verdict"] = {"status": status, "label": label, "summary": summary, "color": color, "tradable": False, "reasons": []}
    return result
