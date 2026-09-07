"""Explicit model boundaries for historical simulations, never live approval."""

from copy import deepcopy


MOMENTUM_DAILY_MODEL = "stock_momentum_daily_selection_v1"
RULE_EXECUTION_MODEL = "daily_rule_fixed_percent_stop_50_50_v1"
LIVE_INPUTS = {
    "completed_5m_trigger": "Abgeschlossene 5-Minuten-Trigger und ihre Frische",
    "completed_4h_execution": "Historischer 4-Stunden-Ausfuehrungszustand",
    "point_in_time_quotes": "Zeitgleiche Quotes, Spread und reale Liquiditaet",
    "point_in_time_context": "Damals verfuegbare Unternehmens-, News- und Marktdaten",
    "live_structural_plan": "Exakter damaliger Entry-, Stop- und Zielplan",
    "broker_fills": "Tatsaechliche Broker-Ausfuehrungen und Kosten",
}


def backtest_methodology(strategy=None, rule=None, *, legacy=False):
    """Describe effective inputs/execution, including empty and failed studies."""
    name = str(strategy or "")
    momentum = bool(rule and rule.get("selection_model") == MOMENTUM_DAILY_MODEL)
    known_daily = bool(rule) or name in {
        "sma_crossover", "ema_crossover", "rsi_mean_reversion", "macd",
        "bollinger_bands", "mean_reversion_sma", "turtle_breakout",
    }
    warnings = [
        "Tagesdaten-Simulation, kein Nachweis fuer den Live-Scanner oder reale Broker-Ergebnisse.",
        "5-Minuten-Trigger, 4-Stunden-Freigabe und damaliger Markt-/Unternehmenskontext sind nicht vollstaendig nachgebildet.",
        "Kosten sind Modellannahmen; Ausfuehrung und Reihenfolge innerhalb einer Tageskerze bleiben unsicher.",
    ]
    if legacy:
        label = "Historischer Cache ohne verifizierte Modellversion"
        selection = "legacy_unversioned_proxy"
        execution = "legacy_unversioned_proxy"
        costs = None
        warnings.insert(0, "Dieser Cache wurde nicht mit dem aktuellen Auswahlvertrag neu berechnet.")
    elif rule:
        label = "Momentum-Tagesauswahl mit modellierten Exits" if momentum else "Tagesdaten-Modell einer Scanner-Regel"
        selection = MOMENTUM_DAILY_MODEL if momentum else "daily_rule_conditions_v1"
        execution = RULE_EXECUTION_MODEL
        costs = {"entry_slippage_bps": 5.0, "exit_slippage_bps": 5.0,
                 "round_trip_fee_bps": 20.0, "source": "fixed_assumption_not_broker_fills"}
        warnings.append("Einstieg und Exits folgen der ausgewiesenen Tagesregel, nicht dem strukturabhaengigen Live-Plan.")
    else:
        label = "Historische Simulation ohne Live-Paritaetsnachweis"
        selection = "strategy_specific_historical_model"
        execution = "strategy_specific_daily_simulation"
        # Different indicator/scanner engines have different fills and costs.
        # Do not attribute the rule engine's cost model to all other engines.
        costs = None
        if not known_daily:
            warnings = [
                "Historische Modell-Simulation, kein Nachweis fuer den Live-Scanner oder reale Broker-Ergebnisse.",
                "Eine vollstaendige Nachbildung aller damaligen Live-Freigaben und Ausfuehrungen ist nicht verifiziert.",
                "Zeitaufloesung, Fuellmodell und Kosten richten sich nach dem jeweiligen Teilmodell.",
            ]
    provenance = {
        "strategy": name,
        "input_timeframe": "1D" if known_daily and not legacy else "strategy_specific_unverified",
        "selection_contract_version": 1 if momentum and not legacy else None,
        "cost_policy": costs,
        "unavailable_live_inputs": list(LIVE_INPUTS) if rule and not legacy else [],
        "unavailable_live_input_labels": list(LIVE_INPUTS.values()) if rule and not legacy else [],
        "unverified_live_inputs": [] if rule and not legacy else list(LIVE_INPUTS),
    }
    if rule and not legacy:
        provenance["execution_rule"] = {
            key: deepcopy(rule.get(key))
            for key in ("entry", "stop_pct", "tp1_rr", "tp2_rr", "max_hold_days")
        }
    return {
        "methodology_label": label,
        "methodology_warnings": warnings,
        "methodology_warning_codes": ["daily_proxy_not_live_validation", "missing_live_execution_inputs"],
        "selection_model": selection,
        "execution_model": execution,
        "live_equivalent": False,
        "live_validation_eligible": False,
        "paper_autotrade_release_eligible": False,
        "model_provenance": provenance,
    }


def attach_backtest_methodology(result, strategy=None, rule=None, *, legacy=False):
    """Attach reporting boundaries without turning a simulation into approval."""
    enriched = dict(result or {})
    metadata = backtest_methodology(strategy, rule, legacy=legacy)
    producer_warnings = list(enriched.get("methodology_warnings") or ())
    codes = list(enriched.get("methodology_warning_codes") or ())
    labels = {
        "volume_spike_proxy_not_historical_catalyst": "Volumenspikes sind nur ein technischer Ersatz, kein Backtest damaliger Katalysator-Nachrichten.",
        "current_static_universe_survivorship_bias": "Das heutige feste Aktienuniversum kann historische Ergebnisse durch Survivorship Bias verzerren.",
    }
    for warning in producer_warnings:
        if not isinstance(warning, str):
            continue
        if "_" in warning and " " not in warning:
            codes.append(warning)
            metadata["methodology_warnings"].append(labels.get(warning,
                "Das Teilmodell meldet weitere methodische Einschraenkungen; siehe Herkunftsangaben."))
        else:
            metadata["methodology_warnings"].append(warning)
    metadata["methodology_warnings"] = list(dict.fromkeys(metadata["methodology_warnings"]))
    metadata["methodology_warning_codes"] = list(dict.fromkeys(metadata["methodology_warning_codes"] + codes))
    existing_provenance = enriched.get("model_provenance")
    if isinstance(existing_provenance, dict):
        metadata["model_provenance"] = {**metadata["model_provenance"], **deepcopy(existing_provenance)}
    if not rule and enriched.get("execution_model") and not legacy:
        metadata["execution_model"] = enriched["execution_model"]
    enriched.update(metadata)
    return limit_backtest_report(enriched)


def limit_backtest_report(result):
    """A positive historical diagnostic is not a green trading release."""
    enriched = deepcopy(result)
    verdict = enriched.get("verdict")
    if isinstance(verdict, dict):
        verdict.update({"tradable": False, "live_validation_eligible": False})
        if verdict.get("status") in {"approved", "selective"} or verdict.get("color") == "green":
            verdict.update({"status": "model_limited", "label": "NUR TAGESMODELL",
                            "color": "gray", "summary": "Positive Modellwerte sind keine Live- oder Paper-Trading-Freigabe.",
                            "reason": "daily_proxy_not_live_validation"})
    decided = enriched.get("total_decided", enriched.get("total_trades"))
    available = isinstance(decided, (int, float)) and decided > 0 and not enriched.get("error")
    enriched["performance_available"] = available
    if not available:
        for key in ("win_rate", "avg_pnl", "total_pnl", "total_return", "sum_pnl", "best_trade", "worst_trade",
                    "avg_win", "avg_loss", "avg_r", "total_r",
                    "best_r", "worst_r", "expectancy", "profit_factor", "max_drawdown",
                    "avg_hold", "tp1_rate", "tp2_rate", "stop_rate", "full_stop_rate",
                    "post_tp1_stop_rate", "eod_rate", "win_rate_upper", "avg_pnl_upper",
                    "total_pnl_upper", "avg_r_upper", "total_r_upper"):
            enriched[key] = None
        enriched["profit_factor_display"] = "Nicht verfuegbar"
        enriched["profit_factor_unbounded"] = False
    return enriched


def describe_cached_backtest(result, strategy=None):
    """Never relabel an old cached cohort as if today's contract generated it."""
    if not isinstance(result, dict) or not result.get("model_provenance"):
        return attach_backtest_methodology(result if isinstance(result, dict) else {}, strategy, legacy=True)
    enriched = deepcopy(result)
    enriched.update({"live_equivalent": False, "live_validation_eligible": False,
                     "paper_autotrade_release_eligible": False})
    return limit_backtest_report(enriched)
