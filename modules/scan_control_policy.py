"""Pure shared pause capability policy for scanner owners.

Dedicated discovery scans restart from fresh data after a pause. Mixed signal
management and protection monitors stay outside the pause controller so their
position, stop and expiry checks cannot be parked with discovery work.
"""
import re


_LEGACY_SCANNERS = frozenset({"strategy_scan", "bi_long", "bi_short"})
DEDICATED_SCANNERS = frozenset({
    "biotech", "bear", "turtle", "orb", "penny_stocks", "volume_spikes",
    "money_flow", "early_movers", "btc_divergenz", "crypto_explosion",
})
_MIXED_POSITION_MANAGEMENT = frozenset({"new_listing", "crypto_trade_signals"})
_PROTECTION_MONITORS = frozenset({
    "penny_positions", "cup_handle_watch", "quote_capability",
    "crash_monitor", "market_context",
})


def capability(name):
    """Return public capability metadata without normalizing an owner identity."""
    supported = False
    protected = False
    reason = "unsupported_scanner"
    resume_policy = None
    if (isinstance(name, str) and len(name) <= 96
            and re.fullmatch(r"[a-z0-9_]+", name)):
        if name in _LEGACY_SCANNERS or name.startswith("strat_"):
            supported = True
            resume_policy = "continue_if_valid"
        elif name in DEDICATED_SCANNERS or name.startswith("crypto_strat_"):
            supported = True
            resume_policy = "restart_fresh"
        elif name in _MIXED_POSITION_MANAGEMENT:
            protected = True
            reason = "mixed_position_management"
        elif name in _PROTECTION_MONITORS:
            protected = True
            reason = "protection_monitor"
    return {
        "supported": supported,
        "unsupported_reason": None if supported else reason,
        "protected": protected,
        "resume_policy": resume_policy,
    }
