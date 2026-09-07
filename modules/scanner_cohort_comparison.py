"""Read-only paired exported-opportunity comparisons; no market-data invention.

Both versions must consume the same explicitly hashed snapshot for every
opportunity. Outcomes/costs are exported evidence, not inferred from a score.
"""

from datetime import datetime, timezone
import hashlib
import json
import math
from collections import defaultdict


SCHEMA_VERSION = 1
STATES = {"DECIDED", "NO_FILL", "UNRESOLVED", "MISSING", "NOT_SELECTED"}


def input_fingerprint(snapshot):
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("Each opportunity needs a nonempty shared input snapshot")
    encoded = json.dumps(snapshot, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Timestamps must be explicit ISO-8601 instants") from exc
    if parsed.tzinfo is None:
        raise ValueError("Timestamps must include their timezone")
    return parsed.astimezone(timezone.utc)


def _number(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not an R value")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("R values must be finite numbers") from exc
    if not math.isfinite(number):
        raise ValueError("R values must be finite numbers")
    return number


def _summary(rows, total_opportunities):
    selected = [row for row in rows if row["selected"]]
    decided = sorted((row for row in selected if row["state"] == "DECIDED"),
                     key=lambda row: (row["exit_at"], row["opportunity_id"]))
    values = [row["net_r"] for row in decided]
    batches = defaultdict(list)
    for row in decided:
        batches[row["exit_at"]].append(row["net_r"])
    simultaneous = any(len(batch) > 1 for batch in batches.values())
    equity = peak = drawdown = 0.0
    loss_streak = max_loss_streak = 0
    for stamp in sorted(batches):
        value = sum(batches[stamp])
        equity += value
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
        loss_streak = loss_streak + 1 if value < 0 else 0
        max_loss_streak = max(max_loss_streak, loss_streak)
    wins = sum(value > 0 for value in values)
    interval = None
    if values:
        n, z = len(values), 1.959963984540054
        p = wins / n
        denominator = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denominator
        margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
        interval = {"lower": max(0.0, center - margin), "upper": min(1.0, center + margin), "confidence": 0.95,
                    "model": "binomial_wilson_unadjusted_for_trade_dependence"}
    gain = sum(value for value in values if value > 0)
    loss = -sum(value for value in values if value < 0)
    factor = gain / loss if loss > 0 else None
    return {
        "opportunities": total_opportunities, "selected": len(selected),
        "decided": len(decided),
        "no_fill": sum(row["state"] == "NO_FILL" for row in selected),
        "unresolved": sum(row["state"] == "UNRESOLVED" for row in selected),
        "missing": sum(row["state"] == "MISSING" for row in selected),
        "outcome_coverage": len(decided) / len(selected) if selected else None,
        "total_net_r": sum(values) if values else None,
        "mean_net_r": sum(values) / len(values) if values else None,
        "win_rate": wins / len(values) if values else None,
        "win_rate_wilson_95": interval,
        "profit_factor": factor,
        "profit_factor_unbounded": bool(values and gain > 0 and loss == 0),
        "profit_factor_display": f"{factor:.4f}" if factor is not None else "unbounded" if gain > 0 else "unavailable",
        "max_drawdown_r": drawdown if values else None,
        "drawdown_model": "simultaneous_exit_batches_equal_r_units_not_portfolio_drawdown",
        "max_consecutive_losses": max_loss_streak if values and not simultaneous else None,
        "max_consecutive_losing_exit_batches": max_loss_streak if values else None,
        "simultaneous_exit_order_unknown": simultaneous,
        "selected_ids": sorted(row["opportunity_id"] for row in selected),
        "decided_ids": sorted(row["opportunity_id"] for row in decided),
    }


def compare_exported_cohorts(export):
    """Compare old/new on exactly the exported opportunity set and window.

    Required manifest: schema_version=1, scanner, market, direction, horizon,
    timeframe, regime (explicit unknown is allowed), input_kind (historical_export
    or synthetic_fixture), window.start/end (timezone-aware), versions.old/new,
    cost_policy (nonempty description), opportunities. Each opportunity has
    opportunity_id/ticker/observed_at/input and old/new objects containing
    input_sha256, selected, state. DECIDED also needs entry_filled=true,
    exit_at, gross_r and roundtrip_cost_r. Absent outcomes/costs are unavailable.
    """
    if not isinstance(export, dict):
        raise ValueError("Comparison export must be a JSON object")
    if type(export.get("schema_version")) is not int or export["schema_version"] != SCHEMA_VERSION:
        raise ValueError("Unsupported comparison schema_version")
    for key in ("scanner", "market", "direction", "horizon", "timeframe", "regime", "cost_policy"):
        if not isinstance(export.get(key), str) or not export[key].strip():
            raise ValueError(f"Missing explicit {key}")
    if export["direction"] not in {"LONG", "SHORT"}:
        raise ValueError("direction must be LONG or SHORT")
    if export.get("input_kind") not in {"historical_export", "synthetic_fixture"}:
        raise ValueError("input_kind must identify historical_export or synthetic_fixture")
    versions = export.get("versions") or {}
    if not isinstance(versions, dict):
        raise ValueError("versions must be an object with old/new identifiers")
    if set(versions) != {"old", "new"} or any(not isinstance(value, str) or not value.strip() for value in versions.values()):
        raise ValueError("Exactly two explicit version identifiers are required")
    if versions["old"] == versions["new"]:
        raise ValueError("Old/new versions must be distinct")
    window = export.get("window") or {}
    if not isinstance(window, dict):
        raise ValueError("window must be an object with start/end instants")
    start, end = _instant(window.get("start")), _instant(window.get("end"))
    if start >= end:
        raise ValueError("Comparison window must have positive duration")
    opportunities = export.get("opportunities")
    if not isinstance(opportunities, list):
        raise ValueError("opportunities must be an explicit list")
    seen = set()
    results = {"old": [], "new": []}
    fingerprints = []
    for row in opportunities:
        if not isinstance(row, dict):
            raise ValueError("Every opportunity must be a JSON object")
        identity = row.get("opportunity_id")
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError("Opportunity identities must be unique and nonempty")
        seen.add(identity)
        if not isinstance(row.get("ticker"), str) or not row["ticker"].strip():
            raise ValueError("Each opportunity requires a ticker")
        observed = _instant(row.get("observed_at"))
        if not start <= observed < end:
            raise ValueError("Opportunity outside the common comparison window")
        fingerprint = input_fingerprint(row.get("input"))
        fingerprints.append((identity, fingerprint))
        for side in ("old", "new"):
            result = row.get(side)
            if not isinstance(result, dict) or result.get("input_sha256") != fingerprint:
                raise ValueError("Both versions must identify the exact same shared snapshot")
            if not isinstance(result.get("selected"), bool):
                raise ValueError("Both versions need an explicit selected boolean")
            selected = result["selected"]
            state = result.get("state", "MISSING")
            if state not in STATES or (not selected and state != "NOT_SELECTED") or (selected and state == "NOT_SELECTED"):
                raise ValueError("Selection and outcome state are inconsistent")
            item = {"opportunity_id": identity, "selected": selected, "state": state}
            if state == "DECIDED":
                if result.get("entry_filled") is not True:
                    raise ValueError("A decided trade requires an explicitly filled entry")
                gross, cost = _number(result.get("gross_r")), _number(result.get("roundtrip_cost_r"))
                if cost is not None and cost < 0:
                    raise ValueError("Roundtrip cost cannot be negative")
                if gross is None or cost is None or not result.get("exit_at"):
                    item["state"] = "MISSING"
                else:
                    exit_at = _instant(result["exit_at"])
                    if not observed <= exit_at <= end:
                        raise ValueError("Outcome must fall within the common observation horizon")
                    item.update({"net_r": gross - cost, "exit_at": exit_at})
            elif state == "NO_FILL" and result.get("entry_filled") is not False:
                raise ValueError("NO_FILL requires entry_filled=false")
            results[side].append(item)
    old, new = (_summary(results[side], len(opportunities)) for side in ("old", "new"))
    old_decided = {row["opportunity_id"]: row for row in results["old"] if row["state"] == "DECIDED"}
    new_decided = {row["opportunity_id"]: row for row in results["new"] if row["state"] == "DECIDED"}
    paired = sorted(old_decided.keys() & new_decided.keys())
    paired_deltas = [new_decided[key]["net_r"] - old_decided[key]["net_r"] for key in paired]
    return {
        "comparison_model": "paired_exported_opportunities_v1",
        "scanner": export["scanner"], "versions": versions, "window": window,
        "cohort": {key: export[key] for key in ("market", "direction", "horizon", "timeframe", "regime")},
        "input_kind": export["input_kind"], "cost_policy": export["cost_policy"],
        "input_set_sha256": hashlib.sha256(json.dumps(sorted(fingerprints)).encode()).hexdigest(),
        "same_input_verified": True, "live_equivalent": False,
        "live_validation_eligible": False, "paper_autotrade_release_eligible": False,
        "empirical_performance_available": export["input_kind"] == "historical_export" and bool(old_decided or new_decided),
        "old": old, "new": new,
        "selection_added": sorted(set(new["selected_ids"]) - set(old["selected_ids"])),
        "selection_removed": sorted(set(old["selected_ids"]) - set(new["selected_ids"])),
        "paired_decided": len(paired),
        "paired_mean_net_r_delta": sum(paired_deltas) / len(paired_deltas) if paired_deltas else None,
        "warnings": [
            "Exportierte Ergebnisse sind keine unabhaengig verifizierten Broker-Fills.",
            "Fehlende, offene und ungefuellte Trades zaehlen nicht als Nullrendite oder Gewinn.",
            "Unvollstaendige Outcome-Abdeckung kann den beobachteten Vergleich verzerren.",
            "Gleichzeitige Exits werden fuer Drawdown gemeinsam gebucht; eine Trade-Reihenfolge wird nicht erfunden.",
            "Das Wilson-Intervall ist binomial und nicht um abhaengige oder ueberlappende Trades bereinigt.",
        ],
    }
