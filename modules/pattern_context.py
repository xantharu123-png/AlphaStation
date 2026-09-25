"""Pure pattern provenance guards, independent of optional signal tracking."""
from collections.abc import Mapping


def is_elliott_pattern_context(row, *, strategy=None):
    """Elliott provenance cannot be promoted by appended generic trade fields."""
    sources = [row] if isinstance(row, Mapping) else []
    if sources and isinstance(row.get("trade_setup"), Mapping):
        sources.append(row["trade_setup"])
    identities = [strategy]
    for source in sources:
        if source.get("signal_kind") == "pattern_context":
            return True
        if any(key in source for key in ("elliott", "elliott_model", "elliott_timeframe")):
            return True
        identities.extend(source.get(key) for key in
                          ("Strategy", "strategy", "scanner", "pattern_type", "model"))
    return any(isinstance(value, str) and (
        value.strip().lower().startswith("elliott")
        or value.strip().lower().startswith("causal_elliott_")
    ) for value in identities)
