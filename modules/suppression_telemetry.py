"""Durable, privacy-safe counters for scanner/mail suppression decisions.

Only code-owned identifiers are accepted.  Symbols, recipients, prices, mail
subjects and free-form provider errors are deliberately outside this schema.
Counters are aggregated into hourly SQLite buckets so the API and background
process can write concurrently without producing one row per candidate.

Public functions are fail-safe: telemetry must never change a scan or delivery
decision.  SQLite WAL plus ``BEGIN IMMEDIATE`` provides atomic cross-process
increments; a process-local lock avoids needless writer contention.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Any, Dict, Optional

__all__ = [
    "ALLOWED_SUPPRESSION_REASONS",
    "ALLOWED_SUPPRESSION_SCANNERS",
    "SUPPRESSION_TELEMETRY_DB_PATH",
    "record_suppressions",
    "load_suppression_summary",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("ALPHA_DATA_DIR", _REPO_ROOT / "data_cache"))
SUPPRESSION_TELEMETRY_DB_PATH: str = os.environ.get(
    "SUPPRESSION_TELEMETRY_DB_PATH",
    str(_DATA_DIR / "suppression_telemetry.sqlite"),
)

_LOCK = threading.Lock()
_DROP_LOCK = threading.Lock()
_DROPPED_WRITE_REASON_OCCURRENCES = 0
_DROPPED_WRITE_BY_PATH: Dict[str, int] = {}
_DROPPED_WRITE_EVENTS_BY_PATH: Dict[str, list[tuple[float, int, str]]] = {}
_DROP_JOURNAL_SUFFIX = ".drops"
_DROP_JOURNAL_MAX_READ_BYTES = 1024 * 1024
_DROP_JOURNAL_LOCK_TIMEOUT_SECONDS = 0.35
_DROP_OVERFLOW_MAX_BYTES = 64 * 1024
_DROP_HEALTH_WINDOW_SECONDS = 24 * 3600
_DROP_RETENTION_SECONDS = 90 * 24 * 3600
_DROP_CLASSES = frozenset({"sqlite_busy", "permission", "io_error"})
_IDENTIFIER_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_REVISION_RE = re.compile(r"^[0-9a-f]{7,40}$")
_READ_LOCK_TIMEOUT_SECONDS = 0.075
_WRITE_LOCK_TIMEOUT_SECONDS = 0.35
_READ_BUSY_TIMEOUT_MS = 75
_WRITE_BUSY_TIMEOUT_MS = 350
_BUCKET_SECONDS = 3600
_RETENTION_SECONDS = 90 * 24 * 3600
_MAX_COUNT_PER_CALL = 1_000_000
_SCHEMA_VERSION = 1

# These are code-owned dimensions, not merely strings which happen to match a
# regular expression.  A lowercase ticker (``aapl``), customer name
# (``alice``) or free-form provider message must therefore still be rejected.
# Callers can map a new code branch to ``unclassified_code_reason`` until this
# registry is deliberately extended and reviewed.
ALLOWED_SUPPRESSION_SCANNERS = frozenset({
    "bear",
    "bi_long",
    "bi_short",
    "biotech",
    "crypto",
    "crypto_explosion",
    "crypto_strategy",
    "crypto_trade_signals",
    "cup_handle_watch",
    "early_movers",
    "mail_pipeline",
    "money_flow",
    "new_listing",
    "orb",
    "penny_positions",
    "penny_stocks",
    "stock_strategy",
    "stocks_intraday",
    "stocks_premarket",
    "stocks_swing",
    "strategy_scan",
    "turtle",
    "unclassified_scanner",
    "volume_spikes",
})

ALLOWED_SUPPRESSION_REASONS = frozenset({
    # Stable fallback. It intentionally contains no fragment of the rejected
    # value, so an unexpected code reason cannot leak an identity.
    "unclassified_code_reason",
    # Mail transport / delivery contract.
    "blocked_etf_content",
    "missing_gmail_config",
    "missing_recipient",
    "smtp_delivery_failed",
    "smtp_delivery_outcome_unknown",
    "startup_cooldown",
    "tracker_delivery_attempt_not_owned",
    "tracker_delivery_contract_unavailable",
    "tracker_delivery_intent_not_sendable",
    "tracker_public_signal_plan_invalid",
    "tracker_public_signal_ref_invalid",
    "tracker_recipient_authorization_changed",
    # Shared selection, dedupe and batching.
    "bearish_ticker_already_alerted",
    "batch_mail_not_sent",
    "cooldown_active",
    "daily_summary_dedupe_active",
    "dedupe_claim_not_owned",
    "duplicate_ticker_in_scan",
    "grade_below_alert_threshold",
    "mail_adjacent_single_candidate_deferred",
    "missing_cooldown_key",
    "missing_ticker",
    "non_common_stock_product",
    "open_equivalent_trade",
    "persistent_dedupe_active",
    "row_claimed_by_parallel_sender",
    "rvol_below_alert_threshold",
    "score_below_alert_threshold",
    # Final causal quote/path/geometry contract.
    "final_executable_price_missing",
    "final_executable_quote_fetch_failed",
    "final_executable_quote_missing",
    "final_executable_quote_stale",
    "final_executable_quote_timestamp_missing",
    "final_execution_depth_too_thin",
    "final_execution_spread_too_wide",
    "final_live_rr_too_low",
    "final_market_path_bar_invalid",
    "final_market_path_bounds_missing",
    "final_market_path_bounds_invalid",
    "final_market_path_duplicate_timestamp",
    "final_market_path_end_gap",
    "final_market_path_fetch_error",
    "final_market_path_fetch_failed",
    "final_market_path_internal_gap",
    "final_market_path_invalid",
    "final_market_path_lookback_too_long",
    "final_market_path_malformed",
    "final_market_path_missing",
    "final_market_path_ohlc_invalid",
    "final_market_path_order_invalid",
    "final_market_path_payload_invalid",
    "final_market_path_result_limit_reached",
    "final_market_path_start_gap",
    "final_market_path_timestamp_missing",
    "final_market_path_truncated",
    "final_market_path_unavailable",
    "final_market_path_access_denied",
    "final_market_path_http_error",
    "final_market_path_other",
    "final_market_path_rate_limited",
    "final_price_invalid",
    "final_price_session_not_executable",
    "final_quote_before_scan_observation",
    "final_quote_before_trigger_observation",
    "final_quote_invalid",
    "final_quote_or_session_stale",
    "final_quote_session_mismatch",
    "final_quote_spread_too_wide",
    "final_quote_stale",
    "final_quote_stale_after_path",
    "final_quote_stale_at_return",
    "final_quote_timestamp_in_future",
    "final_quote_timestamp_in_future_after_path",
    "final_quote_timestamp_in_future_after_handshake",
    "final_quote_timestamp_in_future_at_return",
    "final_quote_timestamp_missing",
    "final_receipt_session_mismatch",
    "final_receipt_session_mismatch_at_return",
    "final_revalidation_exception",
    "final_revalidation_failed",
    "final_risk_invalid",
    "final_scan_observation_in_future",
    "final_scan_observation_missing",
    "final_scan_observation_stale",
    "final_scan_price_source_missing",
    "final_snapshot_unavailable",
    "final_snapshot_access_denied",
    "final_snapshot_fetch_failed",
    "final_snapshot_http_error",
    "final_snapshot_other",
    "final_snapshot_payload_invalid",
    "final_snapshot_rate_limited",
    "final_source_observation_in_future",
    "final_source_observation_source_missing",
    "final_source_observation_timestamp_missing",
    "final_stock_revalidation_exception",
    "final_stock_revalidation_failed",
    "final_stop_and_tp1_touched_since_scan",
    "final_stop_and_tp1_touched_since_trigger",
    "final_stop_touched_since_scan",
    "final_stop_touched_since_trigger",
    "final_ticker_missing",
    "final_tp1_touched_since_scan",
    "final_tp1_touched_since_trigger",
    "final_trade_levels_invalid",
    "final_trade_levels_missing",
    "final_watermark_invalid",
    "final_handshake_invalid",
    "final_incremental_gap",
    "final_live_geometry_invalid",
    "final_already_touched",
    "final_round_limit_reached",
    "final_advance_failed",
    "mail_adjacent_stock_revalidation_exception",
    "mail_adjacent_stock_revalidation_failed",
    # Common model gates.
    "estimated_trade_plan",
    "entry_quality_watch_only",
    "invalid_trade_plan",
    "near_binary_event",
    "trade_health_chase_risk",
    "trade_health_fakeout_risk",
    "trade_health_liquidity_risk",
    "trade_health_no_trade",
    "trade_health_score_below_80",
    "trade_health_wait_for_continuation",
    "trade_health_wait_for_retest",
    "trade_health_wait_for_trigger",
    "trade_health_watch_only",
    "trade_rr_below_threshold",
    # Reviewed code-owned stock/crypto/premarket mail gates.  These values are
    # emitted as constants by classifier helpers, never derived from symbols,
    # recipients or provider text.
    "bottom_entry_extended_wait_retest",
    "crypto_strategy_watch_only",
    "current_candle_green_reclaim",
    "current_candle_red_fade",
    "drop_too_extended_no_chase",
    "early_mover_blowoff_turnover",
    "early_mover_btc_headwind",
    "early_mover_data_warning",
    "early_mover_execution_liquidity_too_thin",
    "early_mover_late_to_tp1",
    "early_mover_no_chase",
    "early_mover_turnover_without_alpha",
    "extended_long_fading_wait_retest",
    "fresh_5m_state_missing_wait_retest",
    "fresh_5m_state_missing_wait_trigger",
    "fresh_5m_state_stale",
    "hard_extended_long_wait_retest",
    "intraday_unconfirmed_pattern",
    "latest_5m_green_reclaim",
    "latest_5m_red_fade",
    "listing_age_not_tradeable",
    "listing_source_unknown",
    "micro_trigger_missing",
    "missing_current_drop",
    "no_crypto_execution_trigger",
    "no_crypto_tradeable_signal",
    "not_a_premarket_row",
    "not_closing_near_low",
    "not_down_enough_for_breakdown",
    "not_holding_highs_after_up_move",
    "not_new_listing_dump",
    "not_tradeable_signal_quality",
    "partial_crypto_data",
    "premarket_extension_too_stretched",
    "premarket_liquidity_below_threshold",
    "premarket_missing_trade_levels",
    "premarket_score_below_threshold",
    "pump_continuation_risk",
    "risk_too_wide",
    "rr_below_alert_threshold",
    "rvol_below_bear_threshold",
    "safety_not_ok",
    "swing_4h_extended_run_wait_retest",
    "swing_4h_rejection_wait_reclaim",
    "swing_4h_state_missing_wait_trigger",
    "swing_current_candle_fading",
    "swing_day_move_exhausted_no_chase",
    "swing_day_move_extended_wait_retest",
    "swing_extended_wait_retest",
    "swing_extended_without_volume_wait_retest",
    "swing_gap_done_premarket_wait_retest",
    "swing_gap_not_holding_open_wait_retest",
    "swing_gap_not_holding_upper_range_wait_retest",
    "swing_gap_wick_rejection_wait_retest",
    "swing_hard_extended_no_chase",
    "swing_momentum_breakout_quality_wait_retest",
    "swing_momentum_not_holding_open_wait_retest",
    "swing_momentum_not_holding_upper_range_wait_retest",
    "swing_momentum_trend_reclaim_gap_wait_retest",
    "swing_momentum_wick_rejection_wait_retest",
    "swing_multi_day_exhausted_no_chase",
    "swing_multi_day_extended_wait_retest",
    "swing_not_holding_highs_after_move",
    "swing_prevday_run_top_entry_wait_retest",
    "swing_short_4h_state_missing_wait_trigger",
    "swing_short_4h_wait_breakdown",
    "swing_short_4h_wait_failed_reclaim",
    "swing_short_bottom_entry_extended_wait_retest",
    "swing_short_current_candle_reclaim",
    "swing_short_day_move_exhausted_no_chase",
    "swing_short_day_move_extended_wait_retest",
    "swing_short_drop_extended_wait_failed_reclaim",
    "swing_short_drop_too_extended_no_chase",
    "swing_short_extended_wait_retest",
    "swing_short_multi_day_exhausted_no_chase",
    "swing_short_multi_day_extended_wait_retest",
    "swing_short_not_closing_weak",
    "swing_short_not_down_enough",
    "swing_short_prevday_run_bottom_entry_wait_retest",
    "swing_top_entry_extended_wait_retest",
    "target_already_missed",
    "top_entry_extended_wait_retest",
    "turn_not_confirmed",
    # Stock strategy mail-quality return codes.
    "momentum_mail_blocked_breakout_continuation_watch",
    "momentum_mail_blocked_breakout_quality_low",
    "momentum_mail_blocked_fakeout_risk",
    "momentum_mail_blocked_late_intraday_chase",
    "momentum_mail_blocked_late_session_without_daily_close",
    "momentum_mail_blocked_missing_breakout_type",
    "momentum_mail_blocked_missing_liquidity_history",
    "momentum_mail_blocked_not_holding_upper_range",
    "momentum_mail_blocked_range_not_near_breakout_high",
    "momentum_mail_blocked_rvol_below_breakout_floor",
    "momentum_mail_blocked_spike_rejected_from_high",
    "momentum_mail_blocked_thin_baseline_liquidity",
    "momentum_mail_blocked_tp1_already_touched_intraday",
    "momentum_mail_blocked_trend_reclaim_not_breakout",
    "momentum_mail_blocked_unknown_breakout_type",
    "momentum_mail_blocked_upper_wick",
    "stock_swing_mail_blocked_4h_extended_run",
    "stock_swing_mail_blocked_4h_rejection",
    "stock_swing_mail_blocked_low_volatility_budget",
    "stock_swing_mail_blocked_missing_4h_state",
    "stock_swing_mail_blocked_severe_business_risk",
    # Structural and market-regime gates.
    "near_structural_barrier_wait_trigger",
    "market_regime_red",
    "market_regime_yellow",
    "regime_cooldown",
    # Crypto early mover / new listing.
    "armed_watch_mail_hard_disabled",
    "daily_dump_watch_dedupe_active",
    "early_mover_action_not_alertable",
    "early_mover_chased_from_entry",
    "early_mover_live_rr_below_threshold",
    "early_mover_not_long",
    "early_mover_retest_not_near_entry",
    "early_mover_wait_entry_confirmation",
    "early_mover_weak_targets",
    "new_listing_dump_watch_emails_disabled",
    "no_new_listing_dump_watch_candidates",
    "no_new_listing_signals",
    "not_active_short_signal",
    "not_active_short_timing",
    "trigger_stale_for_mail",
    # ORB, Bear and Pennystock batch-specific gates.
    "bear_crash_drop_below_threshold",
    "closed_5m_data_missing",
    "closed_5m_data_stale",
    "fresh_closed_5m_trigger_missing",
    "no_fresh_trigger_or_ignition",
    "orb_not_tradeable",
    "orb_breakout_volume_unconfirmed",
    "orb_invalid_target_geometry",
    "orb_no_active_breakout",
    "orb_range_break_stale",
    "orb_recent_hold_weak",
    "orb_tp1_already_reached",
    "orb_waiting_for_entry_confirmation",
    "penny_non_actionable",
    "below_vwap_neighborhood",
    "broad_liquidity_screen_failed",
    "current_snapshot_missing",
    "distribution_or_extension_warning",
    "full_entry_model_not_tradeable",
    "intraday_participation_too_low",
    "intraday_structure_incomplete",
    "intraday_structure_lost",
    "intraday_trend_not_ready",
    "outside_penny_price_band",
    "outside_trigger_neighborhood",
    "technical_trigger_neighborhood",
    "unknown_trigger_rejection",
    "weak_closed_bar_location",
    "scanner_failed",
    "stock_session_not_executable",
    "daily_close_confirmed_watch_only_no_afterhours_entry",
    "buy_trigger_already_processed",
    # Finite Pennystock discovery, structure and mail-adjacent gates.
    "closed_5m_trigger_stale",
    "current_dollar_volume_below_500k",
    "current_dollar_volume_too_low",
    "dump_risk_above_45",
    "entry_quality_below_75",
    "executable_order_size_below_250_usd",
    "fresh_5m_breakout_or_retest_missing",
    "invalid_structure_plan",
    "invalid_trade_geometry",
    "live_ask_too_far_above_trigger",
    "live_price_lost_breakout_confirmation",
    "live_price_lost_retest_structure",
    "live_spread_unknown",
    "missing_entry_or_breakout_structure",
    "net_effective_rr_below_cost_adjusted_minimum",
    "net_tp1_reward_below_cost_adjusted_minimum",
    "no_distinct_structural_tp2_at_acceptable_reward",
    "no_high_confidence_overhead_structure_targets",
    "no_structural_stop_in_valid_risk_band",
    "no_structural_tp1_at_acceptable_reward",
    "no_verified_overhead_structure_targets",
    "overhead_resistance_too_close",
    "price_outside_penny_universe",
    "projected_dollar_volume_below_3m",
    "projected_dollar_volume_too_low",
    "recent_dilution_reverse_split_or_company_risk_filing",
    "rvol_below_1_5",
    "sec_filing_risk_data_unavailable",
    "setup_quality_below_70",
    "spread_above_execution_limit",
    "spread_too_wide_for_monitoring",
    "trade_score_below_80",
    "causal_trigger_observation_missing",
    "closed_5m_trigger_age_missing_or_invalid",
    "final_handshake_quote_unavailable",
    "final_quote_session_not_regular",
    "final_receipt_session_not_regular",
    "final_receipt_session_not_regular_at_return",
    "fresh_closed_5m_trigger_missing",
    "live_entry_chased",
    "live_executable_order_size_below_250_usd",
    "live_net_risk_invalid",
    "live_net_rr_below_minimum",
    "live_net_tp1_rr_below_minimum",
    "live_quote_missing_or_stale",
    "live_spread_missing_or_invalid",
    "live_spread_too_wide",
    "live_trade_geometry_invalid",
    "missing_symbol",
    "planned_risk_invalid",
    # Cup-and-handle next-session five-minute watch owner.
    "cup_next_session_trigger_data_missing",
    "cup_next_session_market_date_invalid",
    "cup_next_session_completed_5m_missing",
    "cup_next_session_5m_order_invalid",
    "cup_next_session_completed_5m_in_future",
    "cup_next_session_completed_5m_stale",
    "cup_next_session_trigger_not_confirmed",
    "cup_next_session_trigger_in_future",
    "cup_next_session_trigger_stale",
    "cup_next_session_lip_not_held",
    "cup_next_session_entry_extended",
    "cup_next_session_latest_5m_weak",
    "cup_next_session_trigger_failed",
    "cup_next_session_trigger_timestamp_missing",
    "cup_next_session_claim_invalid",
    "cup_next_session_promotion_failed",
    "cup_next_session_evaluation_exception",
})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS suppression_buckets (
    bucket_start INTEGER NOT NULL,
    scanner TEXT NOT NULL,
    reason TEXT NOT NULL,
    code_revision TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    event_count INTEGER NOT NULL CHECK (event_count >= 0),
    PRIMARY KEY (bucket_start, scanner, reason, code_revision)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS idx_suppression_last_seen
    ON suppression_buckets(last_seen_at);
"""


def _db_path(db_path: Optional[str] = None) -> Path:
    return Path(str(db_path or SUPPRESSION_TELEMETRY_DB_PATH))


def _storage_writable(path: Path) -> bool:
    """Best-effort capability check without creating or modifying a file."""
    try:
        if path.is_symlink():
            return False
        if path.exists():
            return bool(
                path.is_file()
                and os.access(path, os.W_OK)
                and os.access(path.parent, os.W_OK | os.X_OK)
            )
        ancestor = path.parent
        while not ancestor.exists() and ancestor != ancestor.parent:
            ancestor = ancestor.parent
        return bool(
            ancestor.is_dir()
            and os.access(ancestor, os.W_OK | os.X_OK)
        )
    except OSError:
        return False


def _drop_journal_path(db_path: Optional[str] = None) -> Path:
    path = _db_path(db_path)
    return path.with_name(path.name + _DROP_JOURNAL_SUFFIX)


def _drop_class(exc: BaseException) -> str:
    text = str(exc).lower()
    if isinstance(exc, sqlite3.OperationalError) and (
        "busy" in text or "locked" in text
    ):
        return "sqlite_busy"
    if isinstance(exc, PermissionError) or "permission" in text:
        return "permission"
    return "io_error"


def _record_dropped_write(
    count: int,
    *,
    db_path: Optional[str] = None,
    error_class: str = "io_error",
    observed_at: Optional[float] = None,
) -> None:
    """Best-effort cross-process drop marker with no operational identity."""
    global _DROPPED_WRITE_REASON_OCCURRENCES
    safe_count = max(0, min(_MAX_COUNT_PER_CALL, int(count)))
    if not safe_count:
        return
    safe_class = error_class if error_class in _DROP_CLASSES else "io_error"
    try:
        event_timestamp = float(
            observed_at if observed_at is not None else time.time()
        )
    except (TypeError, ValueError, OverflowError):
        return
    if not math.isfinite(event_timestamp) or event_timestamp <= 0:
        return
    path_key = str(_drop_journal_path(db_path).absolute())
    with _DROP_LOCK:
        _DROPPED_WRITE_REASON_OCCURRENCES += safe_count
        _DROPPED_WRITE_BY_PATH[path_key] = (
            _DROPPED_WRITE_BY_PATH.get(path_key, 0) + safe_count
        )
        events = _DROPPED_WRITE_EVENTS_BY_PATH.setdefault(path_key, [])
        events.append((event_timestamp, safe_count, safe_class))
        retention_cutoff = event_timestamp - _DROP_RETENTION_SECONDS
        _DROPPED_WRITE_EVENTS_BY_PATH[path_key] = [
            event for event in events if event[0] >= retention_cutoff
        ][-4096:]
    try:
        path = _drop_journal_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            return
        lock_fd = _acquire_drop_journal_lock(
            path, timeout=_DROP_JOURNAL_LOCK_TIMEOUT_SECONDS
        )
        if lock_fd is None:
            return
        try:
            target = path
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
            if path.exists() and path.stat().st_size >= _DROP_JOURNAL_MAX_READ_BYTES:
                target = path.with_name(path.name + ".overflow")
                if target.is_symlink() or (
                    target.exists() and not target.is_file()
                ):
                    return
                if (
                    target.exists()
                    and target.stat().st_size >= _DROP_OVERFLOW_MAX_BYTES
                ):
                    # Preserve the newest failure marker under bounded space.
                    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            fd = os.open(str(target), flags, 0o600)
            try:
                payload = (
                    f"{event_timestamp:.6f}\t{safe_count}\t{safe_class}\n"
                ).encode("ascii")
                os.write(fd, payload)
            finally:
                os.close(fd)
        finally:
            _release_drop_journal_lock(path, lock_fd)
    except (OSError, ValueError, OverflowError):
        # Observability can never alter a scanner or delivery decision.
        return


def _load_dropped_write_indicator(
    db_path: Optional[str] = None,
    *,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    timestamp_now = float(now if now is not None else time.time())
    health_cutoff = timestamp_now - _DROP_HEALTH_WINDOW_SECONDS
    retention_cutoff = timestamp_now - _DROP_RETENTION_SECONDS
    path_key = str(_drop_journal_path(db_path).absolute())
    with _DROP_LOCK:
        local_events = list(_DROPPED_WRITE_EVENTS_BY_PATH.get(path_key, []))
    local_window = [event for event in local_events if event[0] >= health_cutoff]
    local_count = sum(event[1] for event in local_window)
    local_last = max(local_window, default=(0.0, 0, None), key=lambda x: x[0])
    result = {
        "count": local_count,
        "last_at": _iso(local_last[0]),
        "last_class": local_last[2],
    }
    try:
        path = _drop_journal_path(db_path)
        if path.is_symlink() or not path.is_file():
            return result
        lock_fd = _acquire_drop_journal_lock(
            path, timeout=_DROP_JOURNAL_LOCK_TIMEOUT_SECONDS
        )
        if lock_fd is None:
            return result
        try:
            return _load_dropped_write_indicator_locked(
                path,
                result=result,
                local_count=local_count,
                local_last=local_last,
                health_cutoff=health_cutoff,
                retention_cutoff=retention_cutoff,
            )
        finally:
            _release_drop_journal_lock(path, lock_fd)
    except (OSError, ValueError, OverflowError):
        return result


def _load_dropped_write_indicator_locked(
    path: Path,
    *,
    result: Dict[str, Any],
    local_count: int,
    local_last: tuple[float, int, Optional[str]],
    health_cutoff: float,
    retention_cutoff: float,
) -> Dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file():
            return result
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode):
            return result
        size = path.stat().st_size
        with path.open("rb") as handle:
            if size > _DROP_JOURNAL_MAX_READ_BYTES:
                handle.seek(size - _DROP_JOURNAL_MAX_READ_BYTES)
                handle.readline()  # discard a possibly partial first record
            lines = handle.readlines()
        overflow_path = path.with_name(path.name + ".overflow")
        overflow_present = False
        if (
            not overflow_path.is_symlink()
            and overflow_path.is_file()
            and stat.S_ISREG(overflow_path.stat().st_mode)
        ):
            overflow_present = True
            with overflow_path.open("rb") as overflow_handle:
                lines.extend(
                    overflow_handle.read(_DROP_OVERFLOW_MAX_BYTES).splitlines(
                        keepends=True
                    )
                )
        journal_count = 0
        last_ts = 0.0
        last_class = None
        retained_lines = []
        stale_or_oversize = (
            size > (_DROP_JOURNAL_MAX_READ_BYTES // 2) or overflow_present
        )
        for raw in lines:
            parts = raw.decode("ascii", errors="ignore").strip().split("\t")
            if len(parts) != 3 or parts[2] not in _DROP_CLASSES:
                continue
            try:
                timestamp = float(parts[0])
                count = int(parts[1])
            except (TypeError, ValueError, OverflowError):
                continue
            if not math.isfinite(timestamp) or timestamp <= 0:
                continue
            if count <= 0 or count > _MAX_COUNT_PER_CALL:
                continue
            if timestamp >= retention_cutoff:
                retained_lines.append(
                    f"{timestamp:.6f}\t{count}\t{parts[2]}\n".encode("ascii")
                )
            else:
                stale_or_oversize = True
            if timestamp >= health_cutoff:
                journal_count += count
                if timestamp >= last_ts:
                    last_ts = timestamp
                    last_class = parts[2]
        # The current process also wrote to the journal, so use the persisted
        # aggregate rather than adding the process-local mirror twice.
        result["count"] = max(local_count, journal_count)
        if last_ts >= local_last[0]:
            result["last_at"] = _iso(last_ts)
            result["last_class"] = last_class
        if stale_or_oversize:
            compacted = _compact_drop_journal_locked(path, retained_lines)
            if compacted and overflow_present:
                try:
                    if (
                        overflow_path.is_file()
                        and not overflow_path.is_symlink()
                    ):
                        overflow_path.unlink()
                except OSError:
                    pass
    except (OSError, ValueError, OverflowError):
        pass
    return result


def _drop_lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".compact.lock")


def _acquire_drop_journal_lock(
    path: Path, *, timeout: float
) -> Optional[int]:
    lock_path = _drop_lock_path(path)
    deadline = time.monotonic() + max(0.0, timeout)
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    while True:
        try:
            fd = os.open(str(lock_path), flags, 0o600)
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                os.close(fd)
                return None
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (BlockingIOError, OSError):
            try:
                os.close(fd)
            except (NameError, OSError):
                pass
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.005)


def _release_drop_journal_lock(path: Path, lock_fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(lock_fd, 0, os.SEEK_SET)
            msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _compact_drop_journal_locked(
    path: Path, retained_lines: list[bytes]
) -> bool:
    """Atomically compact while caller owns the cross-process journal lock."""
    temp_path = path.with_name(
        f"{path.name}.compact.{os.getpid()}.{threading.get_ident()}"
    )
    try:
        if path.is_symlink() or not path.is_file():
            return False
        payload = b"".join(retained_lines)
        if len(payload) > _DROP_JOURNAL_MAX_READ_BYTES:
            payload = payload[-_DROP_JOURNAL_MAX_READ_BYTES:]
            newline = payload.find(b"\n")
            payload = payload[newline + 1:] if newline >= 0 else b""
        temp_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            temp_flags |= os.O_NOFOLLOW
        temp_fd = os.open(str(temp_path), temp_flags, 0o600)
        try:
            if payload:
                os.write(temp_fd, payload)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        if not path.is_symlink() and path.is_file():
            os.replace(str(temp_path), str(path))
            return True
    except (FileExistsError, OSError, ValueError):
        return False
    finally:
        try:
            if temp_path.exists() and not temp_path.is_symlink():
                temp_path.unlink()
        except OSError:
            pass
    return False


@contextmanager
def _local_lock(*, write: bool):
    timeout = (
        _WRITE_LOCK_TIMEOUT_SECONDS if write else _READ_LOCK_TIMEOUT_SECONDS
    )
    acquired = _LOCK.acquire(timeout=timeout)
    if not acquired:
        raise TimeoutError("suppression telemetry lock busy")
    try:
        yield
    finally:
        _LOCK.release()


@contextmanager
def _connection(db_path: Optional[str] = None, *, write: bool = False):
    path = _db_path(db_path)
    # Refuse a final-path symlink.  Telemetry is non-critical; silently losing
    # it is preferable to following an attacker-replaced database path.
    if path.is_symlink():
        raise OSError("suppression telemetry path must not be a symlink")
    busy_timeout_ms = (
        _WRITE_BUSY_TIMEOUT_MS if write else _READ_BUSY_TIMEOUT_MS
    )
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path), timeout=busy_timeout_ms / 1000.0
        )
    else:
        # Health reads must neither create a database/schema nor wait behind a
        # writer for seconds.  Read-only mode also prevents an ostensibly
        # public health request from mutating operational state.
        if not path.is_file():
            raise FileNotFoundError(str(path))
        conn = sqlite3.connect(
            path.resolve().as_uri() + "?mode=ro",
            uri=True,
            timeout=busy_timeout_ms / 1000.0,
        )
    try:
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
        if write:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript(_SCHEMA)
            # ``executescript`` may commit schema setup.  Acquire the writer
            # lock afterwards so the UPSERT batch itself is one immediate
            # cross-process transaction.
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        if write:
            conn.commit()
    except Exception as exc:
        if write:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        conn.close()


def _identifier(value: Any, *, allowed: frozenset[str]) -> Optional[str]:
    token = str(value or "").strip()
    # Code identifiers arrive already normalized.  Never normalize arbitrary
    # input into an accepted value (for example a private uppercase symbol).
    if token != token.lower():
        return None
    if not _IDENTIFIER_RE.fullmatch(token):
        return None
    return token if token in allowed else None


def _revision(value: Any) -> str:
    token = str(value or "").strip().lower()
    return token[:40] if _REVISION_RE.fullmatch(token) else "unknown"


def _iso(timestamp: Any) -> Optional[str]:
    try:
        value = float(timestamp)
        if not math.isfinite(value) or value <= 0:
            return None
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _safe_counts(reasons: Any) -> Dict[str, int]:
    if isinstance(reasons, str):
        reasons = {reasons: 1}
    elif not isinstance(reasons, Mapping):
        try:
            combined: Dict[str, int] = {}
            for reason in reasons or ():
                token = _identifier(
                    reason, allowed=ALLOWED_SUPPRESSION_REASONS
                )
                if token:
                    combined[token] = combined.get(token, 0) + 1
            reasons = combined
        except TypeError:
            return {}
    clean: Dict[str, int] = {}
    for reason, raw_count in reasons.items():
        token = _identifier(reason, allowed=ALLOWED_SUPPRESSION_REASONS)
        if not token:
            continue
        try:
            count = int(raw_count)
        except (TypeError, ValueError, OverflowError):
            continue
        if 0 < count <= _MAX_COUNT_PER_CALL:
            clean[token] = min(
                _MAX_COUNT_PER_CALL,
                clean.get(token, 0) + count,
            )
    return clean


def record_suppressions(
    scanner: Any,
    reasons: Any,
    *,
    code_revision: Any = "unknown",
    observed_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> int:
    """Atomically add aggregate suppression counters; return recorded count.

    Invalid/free-form identifiers are rejected instead of being sanitized into
    something that could accidentally retain a ticker or recipient fragment.
    The function never raises into scanner or mail code.
    """
    scanner_token = _identifier(
        scanner, allowed=ALLOWED_SUPPRESSION_SCANNERS
    )
    counts = _safe_counts(reasons)
    if not scanner_token or not counts:
        return 0
    try:
        timestamp = float(observed_at if observed_at is not None else time.time())
    except (TypeError, ValueError, OverflowError):
        return 0
    if not math.isfinite(timestamp) or timestamp <= 0:
        return 0
    bucket_start = int(timestamp // _BUCKET_SECONDS) * _BUCKET_SECONDS
    revision = _revision(code_revision)
    try:
        with _local_lock(write=True):
            with _connection(db_path, write=True) as conn:
                for reason, count in counts.items():
                    conn.execute(
                        """
                        INSERT INTO suppression_buckets (
                            bucket_start, scanner, reason, code_revision,
                            first_seen_at, last_seen_at, event_count
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(bucket_start, scanner, reason, code_revision)
                        DO UPDATE SET
                            first_seen_at = MIN(first_seen_at, excluded.first_seen_at),
                            last_seen_at = MAX(last_seen_at, excluded.last_seen_at),
                            event_count = event_count + excluded.event_count
                        """,
                        (
                            bucket_start,
                            scanner_token,
                            reason,
                            revision,
                            timestamp,
                            timestamp,
                            count,
                        ),
                    )
                conn.execute(
                    "DELETE FROM suppression_buckets WHERE last_seen_at < ?",
                    (timestamp - _RETENTION_SECONDS,),
                )
        return sum(counts.values())
    except Exception as exc:
        # Process-local, privacy-safe observability for fail-open telemetry.
        # No scanner/reason values are retained when a persistence attempt
        # fails, and the mail/scanner path still returns immediately.
        _record_dropped_write(
            sum(counts.values()),
            db_path=db_path,
            error_class=_drop_class(exc),
        )
        return 0


def load_suppression_summary(
    *,
    hours: int = 24,
    limit: int = 30,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Return privacy-safe reason occurrences, never signal/mail totals.

    A candidate can increment several independent suppression reasons.  The
    aggregate therefore deliberately uses ``reason_occurrences`` as its unit;
    ``total_count`` remains only as a backwards-compatible alias.
    """
    safe_hours = 24
    path = _db_path(db_path)
    dropped_indicator = {"count": 0, "last_at": None, "last_class": None}
    dropped_writes = 0
    try:
        safe_hours = max(1, min(24 * 90, int(hours)))
        limit = max(1, min(100, int(limit)))
        timestamp = float(now if now is not None else time.time())
        if not math.isfinite(timestamp) or timestamp <= 0:
            raise ValueError("invalid time")
        dropped_indicator = _load_dropped_write_indicator(
            db_path, now=timestamp
        )
        dropped_writes = int(dropped_indicator["count"])
        writable = _storage_writable(path)
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise OSError("invalid suppression telemetry database path")
        if not path.exists():
            # First boot with no suppressions is healthy-empty, not a telemetry
            # outage.  Writability is reported independently so operators can
            # distinguish an empty stream from a path that cannot persist the
            # first counter.
            return {
                "available": True,
                "initialized": False,
                "writable": writable,
                "status": (
                    "healthy" if writable and not dropped_writes else "degraded"
                ),
                "schema_version": _SCHEMA_VERSION,
                "window_hours": safe_hours,
                "window_semantics": "approximate_hour_bucket_by_last_seen",
                "window_is_approximate": True,
                "window_start_at": _iso(timestamp - safe_hours * 3600),
                "window_start_bucket_at": _iso(
                    int((timestamp - safe_hours * 3600) // _BUCKET_SECONDS)
                    * _BUCKET_SECONDS
                ),
                "count_unit": "reason_occurrences",
                "reason_occurrences": 0,
                "total_count": 0,
                "dropped_write_reason_occurrences_window": dropped_writes,
                "dropped_write_reason_occurrences": dropped_writes,
                "last_drop_at": dropped_indicator["last_at"],
                "last_dropped_write_at": dropped_indicator["last_at"],
                "last_drop_class": dropped_indicator["last_class"],
                "first_seen_at": None,
                "last_seen_at": None,
                "by_scanner": [],
                "top_reasons": [],
            }
        since = timestamp - safe_hours * 3600
        with _local_lock(write=False):
            with _connection(db_path) as conn:
                total_row = conn.execute(
                    """
                    SELECT COALESCE(SUM(event_count), 0) AS total_count,
                           MIN(first_seen_at) AS first_seen_at,
                           MAX(last_seen_at) AS last_seen_at
                    FROM suppression_buckets
                    WHERE last_seen_at >= ?
                    """,
                    (since,),
                ).fetchone()
                scanner_rows = conn.execute(
                    """
                    SELECT scanner, SUM(event_count) AS event_count,
                           MAX(last_seen_at) AS last_seen_at
                    FROM suppression_buckets
                    WHERE last_seen_at >= ?
                    GROUP BY scanner
                    ORDER BY event_count DESC, scanner ASC
                    """,
                    (since,),
                ).fetchall()
                reason_rows = conn.execute(
                    """
                    SELECT scanner, reason, code_revision,
                           SUM(event_count) AS event_count,
                           MIN(first_seen_at) AS first_seen_at,
                           MAX(last_seen_at) AS last_seen_at
                    FROM suppression_buckets
                    WHERE last_seen_at >= ?
                    GROUP BY scanner, reason, code_revision
                    ORDER BY event_count DESC, last_seen_at DESC,
                             scanner ASC, reason ASC
                    LIMIT ?
                    """,
                    (since, limit),
                ).fetchall()
        reason_occurrences = int(total_row["total_count"] or 0)
        return {
            "available": True,
            "initialized": True,
            "writable": writable,
            "status": (
                "healthy" if writable and not dropped_writes else "degraded"
            ),
            "schema_version": _SCHEMA_VERSION,
            "window_hours": safe_hours,
            "window_semantics": "approximate_hour_bucket_by_last_seen",
            "window_is_approximate": True,
            "window_start_at": _iso(since),
            "window_start_bucket_at": _iso(
                int(since // _BUCKET_SECONDS) * _BUCKET_SECONDS
            ),
            "count_unit": "reason_occurrences",
            "reason_occurrences": reason_occurrences,
            "total_count": reason_occurrences,
            "dropped_write_reason_occurrences_window": dropped_writes,
            "dropped_write_reason_occurrences": dropped_writes,
            "last_drop_at": dropped_indicator["last_at"],
            "last_dropped_write_at": dropped_indicator["last_at"],
            "last_drop_class": dropped_indicator["last_class"],
            "first_seen_at": _iso(total_row["first_seen_at"]),
            "last_seen_at": _iso(total_row["last_seen_at"]),
            "by_scanner": [
                {
                    "scanner": str(row["scanner"]),
                    "count": int(row["event_count"] or 0),
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in scanner_rows
            ],
            "top_reasons": [
                {
                    "scanner": str(row["scanner"]),
                    "reason": str(row["reason"]),
                    "code_revision": str(row["code_revision"]),
                    "count": int(row["event_count"] or 0),
                    "first_seen_at": _iso(row["first_seen_at"]),
                    "last_seen_at": _iso(row["last_seen_at"]),
                }
                for row in reason_rows
            ],
        }
    except Exception:
        return {
            "available": False,
            "initialized": bool(path.is_file() and not path.is_symlink()),
            "writable": _storage_writable(path),
            "status": "unavailable",
            "schema_version": _SCHEMA_VERSION,
            "window_hours": safe_hours,
            "window_semantics": "approximate_hour_bucket_by_last_seen",
            "window_is_approximate": True,
            "window_start_at": None,
            "window_start_bucket_at": None,
            "count_unit": "reason_occurrences",
            "reason_occurrences": 0,
            "total_count": 0,
            "dropped_write_reason_occurrences_window": dropped_writes,
            "dropped_write_reason_occurrences": dropped_writes,
            "last_drop_at": dropped_indicator["last_at"],
            "last_dropped_write_at": dropped_indicator["last_at"],
            "last_drop_class": dropped_indicator["last_class"],
            "first_seen_at": None,
            "last_seen_at": None,
            "by_scanner": [],
            "top_reasons": [],
        }
