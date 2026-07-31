"""Reasons-Registry-Guard (AUDIT 2026-07-31, BHC-Folge).

Der BHC-Fast-Fehler: Ein neu erzeugter Gate-Grund
("swing_multi_day_exhausted_no_chase") waere ohne explizite Registrierung
in _alert_decision_from_reasons als "WATCH" gelabelt statt "NO_TRADE" —
das Decision-Mapping ist KEINE Suffix-Logik, sondern eine explizite Menge.

Dieser Guard macht die Luecke strukturell unmoeglich:
  1. Jeder in api.py erzeugte Gate-Grund (reasons.append("...")) muss im
     Decision-Mapping zu einer nicht-WATCH-Entscheidung fuehren — oder in
     der unten eingefrorenen WATCH-Whitelist stehen.
  2. Die Whitelist ist bidirektional: verwaiste Eintraege (Grund existiert
     nicht mehr oder ist inzwischen gemappt) schlagen ebenfalls an.
  => Wer einen neuen Grund einfuehrt, MUSS ihn entweder mappen oder
     bewusst whitelisten — beides ist eine dokumentierte Entscheidung.

Erfasst werden Literale der Form reasons.append("snake_case") — dynamische
Grundstrings (f-Strings) sind vom Decision-Pfad ohnehin praefix-gedeckt
(no_trade_prefixes) und hier nicht pruefbar.
"""

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import api  # noqa: E402

_SCANNERS = ("stock_strategy", "bear", "early_movers", "new_listing", "crypto_strategy", "biotech")

# Eingefrorener Ist-Stand 2026-07-31: Gründe, die aktuell bewusst(?) WATCH
# ergeben. NICHT als Freispruch lesen — drei Kandidaten sind im Audit-Dokument
# als Beobachtungspunkte markiert (crash_drop_too_extended,
# rvol_below_bear_threshold, swing_short_not_down_enough).
KNOWN_WATCH_REASONS = frozenset({
    "action_not_armed",
    "already_trade_triggered",
    "bearish_ticker_already_alerted",
    "bounce_after_recent_selloff",
    "btc_not_tailwind",
    "cooldown_active",
    "crash_drop_too_extended",
    "crash_drop_too_small",
    "crash_not_pressing_lows",
    "crash_rvol_below_threshold",
    "crypto_strategy_watch_only",
    "current_candle_green_reclaim",
    "daily_close_not_near_high",
    "daily_momentum_too_small",
    "data_not_clean",
    "early_mover_action_not_alertable",
    "early_mover_btc_headwind",
    "early_mover_data_warning",
    "early_mover_execution_liquidity_too_thin",
    "early_mover_live_rr_below_threshold",
    "early_mover_not_long",
    "early_mover_weak_targets",
    "entry_quality_watch_only",
    "entry_score_below_armed_threshold",
    "explosion_score_below_threshold",
    "fresh_5m_state_stale",
    "htf_active_red_candle",
    "htf_context_missing",
    "htf_current_bar_rejecting",
    "htf_green_run_extended",
    "htf_lower_high_after_sweep",
    "htf_move_already_extended",
    "htf_not_compressed",
    "htf_pullback_after_spike",
    "htf_range_not_tightening",
    "htf_rebound_from_lows_already_extended",
    "htf_two_red_after_spike",
    "intraday_unconfirmed_pattern",
    "latest_5m_green_reclaim",
    "latest_5m_red_fade",
    "listing_source_unknown",
    "live_rr_below_armed_threshold",
    "micro_trigger_missing",
    "near_binary_event",
    "no_crypto_execution_trigger",
    "no_crypto_tradeable_signal",
    "no_ema20_50_trend_reclaim",
    "no_momentum_breakout_structure",
    "not_a_premarket_row",
    "not_active_short_signal",
    "not_active_short_timing",
    "not_closing_near_low",
    "not_down_enough_for_breakdown",
    "not_enough_daily_history",
    "not_near_4h_breakout_level",
    "not_pre_breakout_coil",
    "persistent_dedupe_active",
    "pre_breakout_score_below_threshold",
    "premarket_extension_too_stretched",
    "premarket_liquidity_below_threshold",
    "premarket_missing_trade_levels",
    "premarket_score_below_threshold",
    "price_below_ema20",
    "risk_high",
    "rr_below_alert_threshold",
    "rsi_overheated",
    "rsi_too_weak_for_momentum",
    "rvol_below_alert_threshold",
    "rvol_below_bear_threshold",
    "rvol_below_breakout_threshold",
    "score_below_alert_threshold",
    "setup_score_below_armed_threshold",
    "spread_too_wide",
    "swing_short_not_down_enough",
    "thin_book_10bps",
    "thin_book_25bps",
    "thin_book_50bps",
    "too_far_from_entry_for_armed",
    "tp1_already_reached",
    "trade_health_score_below_80",
    "turn_not_confirmed",
})


def _collect_reason_literals() -> set:
    src = (REPO_ROOT / "api.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    literals = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "reasons"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            value = node.args[0].value
            if re.fullmatch(r"[a-z0-9_]+", value):
                literals.add(value)
    return literals


def _decisions_for(reason: str) -> set:
    return {
        api._alert_decision_from_reasons(scanner, [reason])["decision"]
        for scanner in _SCANNERS
    }


def test_reason_inventory_is_substantial():
    # Sanity: das Inventar darf nicht versehentlich leer laufen (Parser-Bruch).
    assert len(_collect_reason_literals()) >= 100


def test_every_reason_is_mapped_or_whitelisted():
    """Neuer Grund ohne Mapping → Test schlaegt an (BHC-Luecke geschlossen)."""
    unmapped = []
    for reason in sorted(_collect_reason_literals()):
        if _decisions_for(reason) == {"WATCH"} and reason not in KNOWN_WATCH_REASONS:
            unmapped.append(reason)
    assert not unmapped, (
        "Neue Gate-Gruende ohne Decision-Mapping (ergeben WATCH): "
        f"{unmapped}. In _alert_decision_from_reasons registrieren "
        "(no_trade/wait_retest/wait_trigger) ODER bewusst in "
        "KNOWN_WATCH_REASONS aufnehmen."
    )


def test_whitelist_has_no_stale_entries():
    """Whitelist-Eintraege fuer nicht mehr existente oder inzwischen gemappte
    Gruende muessen entfernt werden — die Liste bleibt die ehrliche Doku."""
    literals = _collect_reason_literals()
    stale_missing = sorted(KNOWN_WATCH_REASONS - literals)
    stale_mapped = sorted(
        reason for reason in KNOWN_WATCH_REASONS & literals
        if _decisions_for(reason) != {"WATCH"}
    )
    assert not stale_missing, f"Whitelist-Gruende existieren nicht mehr im Code: {stale_missing}"
    assert not stale_mapped, f"Whitelist-Gruende sind inzwischen gemappt (aus Liste nehmen): {stale_mapped}"
