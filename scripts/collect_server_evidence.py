#!/usr/bin/env python3
"""Standalone read-only server evidence export; stdlib only, no app-code import.

Run the reviewed local source through `ssh ... python3 -I -`. Only stdout is
written. No DB migration, cache update, scan, market request, mail or order.
The projected tracker rows are private audit data, NOT a public API response.
"""
import argparse
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
import http.client
import math
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import subprocess
import sys


# Same arithmetic allowlist as signal_performance_breakdown. No recipients,
# account blobs, tokens, mail body or environment contents are exported.
TRACKER_COLUMNS = """
id created_at scanner mail_class channel status asset_class direction strategy
trade_horizon evaluation_horizon_bars market_regime code_revision
evaluation_model_version fill_evidence_mode path_extrema_evidence_mode
origin_evidence public_signal_ref delivery_accepted_at entry stop tp1 tp2
entry_filled_at entry_fill_price closed_at r_realized r_realized_upper
tp1_hit_at tp2_hit_at stop_hit_at outcome_detail max_favorable_r
be_trigger_at be_activated_at be_mail_sent_at be_delivery_evidence_key
be_exit_at be_exit_fill_price be_exit_evidence_mode be_exit_tp1_order
stop_gap_slippage_r
""".split()
PATH_ENV = {"ALPHA_DATA_DIR", "SIGNAL_TRACKER_DB_PATH", "ALPHA_RUNTIME_TMP_DIR",
            "MAIL_OUTBOX_DB_PATH", "SUPPRESSION_TELEMETRY_DB_PATH", "SIGNAL_DELIVERY_JOURNAL_DB_PATH"}
REQUIRED_COLUMNS = {"id", "created_at", "scanner", "mail_class", "status"}
CACHE_STATUSES = frozenset({"scanning", "running", "done", "error", "stopped", "idle", "pending"})
DIAGNOSTIC_COUNTS = frozenset("""
total checked history_available analyzed indicator_passed data_failures
analysis_errors final_results universe_count common_stock_universe_count
raw_matches_before_special_filter max_results quarantined_symbols
special_filter_input_count special_filter_checked_count special_filter_unexamined_count special_filter_limit
transport_requests transport_retries transport_recovered_incidents transport_retry_budget_exhausted
excluded_uncompleted_bars
""".split())
STAGE_COUNTS = frozenset("""
snapshot_universe valid_symbol_and_prev_close common_stock_asset priced_snapshot
change_filter price_filter close_position_filter gap_filter dollar_volume_filter
vortag_filter rvol_filter momentum_breakout_gate momentum_completed_5m_confirmation
reversal_ad_gate raw_matches_before_special_filter final_results
wyckoff_analyzed wyckoff_confirmed
""".split())
# These are exact codes, never a wildcard for provider messages or ticker names.
DATA_FAILURE_CODES = frozenset("""
scan_data_unavailable scan_provider_unauthorized scan_provider_rate_limited
scan_provider_error scan_data_invalid scan_data_incomplete scan_analysis_failed exception
""".split())
PUBLIC_SCAN_ERROR_CODES = frozenset("""
scan_data_unavailable scan_provider_unauthorized scan_provider_rate_limited
scan_data_incomplete scan_data_invalid
""".split())
DATA_ERROR_REASONS = frozenset("""
invalid_payload provider_status invalid_json missing_results invalid_results_type
invalid_result_count invalid_query_count result_count_mismatch contradictory_empty_response
unexpected_pagination invalid_bar_type invalid_bar_value invalid_bar_geometry
invalid_bar_timestamp invalid_data_conversion
""".split())
TRANSPORT_ERROR_REASONS = frozenset("""
timeout connection_failure tls_failure http_unauthorized http_rate_limited
http_request_timeout http_server_error http_client_error http_unexpected_status
malformed_json unexpected_failure
""".split())
REJECTION_CODES = DATA_FAILURE_CODES | frozenset("""
insufficient_daily_history insufficient_completed_history insufficient_dollar_liquidity
rvol_anomaly spac_nav already_broke_out cumulative_pump indicator_or_hard_gate_contract
invalid_symbol_or_missing_prev_close missing_price_or_prev_close change_filter
price_filter close_position_filter gap_filter dollar_volume_filter vortag_filter
rvol_filter momentum_breakout_gate premarket_extension_guard reversal_ad_gate
premarket_dollar_volume_filter premarket_missing_quote premarket_spread_guard
plan:invalid plan:insufficient_history plan:invalid_ohlc plan:invalid_live_price
plan:range_too_narrow plan:atr_too_small plan:entry_too_extended
plan:structure_cutoff_missing plan:structural_barrier_blocked plan:invalid_geometry_or_rr
momentum:not_enough_daily_history momentum:invalid_momentum_inputs
momentum:daily_momentum_too_small momentum:rvol_below_breakout_threshold
momentum:daily_close_not_near_high momentum:no_momentum_breakout_structure
momentum:price_below_ema20 momentum:no_ema20_50_trend_reclaim
momentum:rsi_too_weak_for_momentum momentum:rsi_overheated
momentum:bounce_after_recent_selloff momentum:incoherent_signal_direction
momentum:intraday_unavailable momentum:intraday_stale momentum:intraday_not_confirmed
momentum:intraday_failed_breakout momentum:intraday_stale_breakout
momentum:intraday_stale_extension momentum:intraday_data_unavailable
momentum:intraday_confirmation_stale momentum:confirmation_expired_before_publication
reversal_ad:ad_confirms_selloff_falling_knife
wyckoff:event_sequence_unconfirmed_or_invalid
""".split())
CACHE_MAX_BYTES = 8 * 1024 * 1024
CRYPTO_SCAN_COUNTS = frozenset("""
universe_count chart_checked max_chart_checks venue_workers result_count trade_now_count armed_count
""".split())
DATA_ERROR_VALUE_CLASSES = frozenset("""
missing null boolean non_numeric non_finite zero_price negative_price negative_volume
nonpositive_timestamp nonascending_timestamp timestamp_out_of_range future_timestamp invalid_geometry unknown
""".split())
DATA_ERROR_POSITIONS = frozenset({"only", "first", "interior", "last", "unknown"})
SCAN_MAIL_EVENTS = frozenset(f"{kind}_{event}" for kind in ("trade", "other")
                            for event in ("sender_called", "accepted", "partial", "partial_unknown", "unknown", "failed", "queued"))
SCAN_MAIL_SEMANTICS = "overlapping_reason_occurrences_and_message_events_not_inbox_delivery"
CRYPTO_VENUES = frozenset({"bybit", "binance", "mexc", "bitget"})
CRYPTO_CACHE_NAMES = frozenset({"crypto_explosion_cache.json", "crypto_trade_signals_cache.json"})
CRYPTO_ROW_STATES = {
    "trade_action": frozenset({"JETZT_LONG", "JETZT_SHORT", "LONG_ARMED", "SHORT_WATCH"}),
    "trade_signal": frozenset({"JETZT_TRADEN", "EXPLOSION_ARMED", "WARTEN"}),
}
PLAN_BUILD_CODES = frozenset("""
invalid_entry_or_direction causal_structure_missing causal_structure_unavailable
crossed_resistance_unconfirmed crossed_support_unconfirmed no_structural_invalidation
invalid_stop_risk invalid_trade_geometry native_structure_plan
first_opposing_barrier_before_minimum_rr direction_missing plan_unavailable
""".split())
CUP_TERMINAL_REASONS = frozenset("""
special_filter_accepted missing_symbol insufficient_completed_history
liquidity_below_floor invalid_pattern_data invalid_current_price pattern_unconfirmed
breakout_close_unconfirmed entry_extension_rejected breakout_volume_unconfirmed
handle_volume_unconfirmed trade_plan_unconfirmed pattern_score_below_threshold
blended_score_below_threshold entry_quality_rejected other_special_filter_rejected
""".split())
CUP_TERMINAL_COUNT_SEMANTICS = (
    "one_outcome_per_checked_candidate_detector_deepest_stage_not_native_plan_or_mail"
)
WYCKOFF_REASONS = frozenset("""
confirmed event_sequence_unconfirmed minimum_completed_bars_missing
invalid_bar_timestamp invalid_bar_payload invalid_bar_volume invalid_bar_prices
invalid_bar_value conflicting_completed_bars positive_volume_evidence_missing atr_unavailable
range_failed range_failed_before_secondary_test spring_volume_unavailable
breakout_failed confirmation_atr_unavailable post_confirmation_stop_breached
latest_volume_evidence_missing projected_target_not_beyond_entry invalid_trade_geometry
conflicting_directional_patterns
""".split())
# Mirrored protocol keys only. The root-invoked collector never imports app code.
CONFLUENCE_COUNTS = frozenset("""
evaluated schema_invalid below_required incomplete pre_hard_gate_qualified
core_valid_count payload_accepted_count observation_errors
""".split())
CONFLUENCE_FACTORS = frozenset("""
atr_squeeze volume_dry_up obv_flow close_position range_duration boundary_tests
adx_turning institutional_flow rsi_drift range_structure directional_persistence
range_compression macd_histogram stochastic_momentum order_block_confluence
fvg_proximity liquidity_pool_proximity fibonacci_confluence volume_void
candle_body_compression
""".split())
CONFLUENCE_FACTOR_COUNTS = frozenset({"evaluated", "green", "red", "unavailable"})
CONFLUENCE_HARD_GATES = frozenset({
    "last_bar_pump", "range_breakdown", "recent_bearish_pressure",
    "recent_bullish_pressure", "unknown",
})
CONFLUENCE_CONTRACTS = frozenset({"stock-bi-20-v2", "stock-bi-20-v3"})
BI_CACHE_SCANNERS = {
    "bi_cache_long.json": "bi_long", "bi_cache_long.json.partial": "bi_long",
    "bi_cache_short.json": "bi_short", "bi_cache_short.json.partial": "bi_short",
    "bi_scan_progress_long.json": "bi_long", "bi_scan_progress_short.json": "bi_short",
}


# Reviewed code-owned dimensions mirrored without importing the application.
SUPPRESSION_SCANNERS = frozenset("""
bear
bi_long
bi_short
biotech
crypto
crypto_explosion
crypto_strategy
crypto_trade_signals
cup_handle_watch
early_movers
mail_pipeline
money_flow
new_listing
orb
penny_positions
penny_stocks
stock_strategy
stocks_intraday
stocks_premarket
stocks_swing
strategy_scan
turtle
unclassified_scanner
volume_spikes
""".split())
SUPPRESSION_REASONS = frozenset("""
wyckoff_contract_invalid
armed_watch_mail_hard_disabled
batch_mail_not_sent
bear_crash_drop_below_threshold
bearish_ticker_already_alerted
below_vwap_neighborhood
blocked_etf_content
bottom_entry_extended_wait_retest
broad_liquidity_screen_failed
buy_trigger_already_processed
causal_trigger_observation_missing
closed_5m_data_missing
closed_5m_data_stale
closed_5m_trigger_age_missing_or_invalid
closed_5m_trigger_stale
cooldown_active
crypto_strategy_watch_only
cup_next_session_5m_order_invalid
cup_next_session_claim_invalid
cup_next_session_completed_5m_in_future
cup_next_session_completed_5m_missing
cup_next_session_completed_5m_stale
cup_next_session_entry_extended
cup_next_session_evaluation_exception
cup_next_session_latest_5m_weak
cup_next_session_lip_not_held
cup_next_session_market_date_invalid
cup_next_session_promotion_failed
cup_next_session_trigger_data_missing
cup_next_session_trigger_failed
cup_next_session_trigger_in_future
cup_next_session_trigger_not_confirmed
cup_next_session_trigger_stale
cup_next_session_trigger_timestamp_missing
current_candle_green_reclaim
current_candle_red_fade
current_dollar_volume_below_500k
current_dollar_volume_too_low
current_snapshot_missing
daily_close_confirmed_watch_only_no_afterhours_entry
daily_dump_watch_dedupe_active
daily_summary_dedupe_active
dedupe_claim_not_owned
distribution_or_extension_warning
drop_too_extended_no_chase
dump_risk_above_45
duplicate_ticker_in_scan
early_mover_action_not_alertable
early_mover_blowoff_turnover
early_mover_btc_headwind
early_mover_chased_from_entry
early_mover_data_warning
early_mover_execution_liquidity_too_thin
early_mover_late_to_tp1
early_mover_live_rr_below_threshold
early_mover_no_chase
early_mover_not_long
early_mover_retest_not_near_entry
early_mover_turnover_without_alpha
early_mover_wait_entry_confirmation
early_mover_weak_targets
entry_quality_below_75
entry_quality_watch_only
estimated_trade_plan
executable_order_size_below_250_usd
extended_long_fading_wait_retest
final_advance_failed
final_already_touched
final_executable_price_missing
final_executable_quote_fetch_failed
final_executable_quote_missing
final_executable_quote_stale
final_executable_quote_timestamp_missing
final_execution_depth_too_thin
final_execution_spread_too_wide
final_handshake_invalid
final_handshake_quote_unavailable
final_incremental_gap
final_live_geometry_invalid
final_live_rr_too_low
final_market_path_access_denied
final_market_path_bar_invalid
final_market_path_bounds_invalid
final_market_path_bounds_missing
final_market_path_duplicate_timestamp
final_market_path_end_gap
final_market_path_fetch_error
final_market_path_fetch_failed
final_market_path_http_error
final_market_path_internal_gap
final_market_path_invalid
final_market_path_lookback_too_long
final_market_path_malformed
final_market_path_missing
final_market_path_ohlc_invalid
final_market_path_order_invalid
final_market_path_other
final_market_path_payload_invalid
final_market_path_rate_limited
final_market_path_result_limit_reached
final_market_path_start_gap
final_market_path_timestamp_missing
final_market_path_truncated
final_market_path_unavailable
final_price_invalid
final_price_session_not_executable
final_quote_before_scan_observation
final_quote_before_trigger_observation
final_quote_invalid
final_quote_or_session_stale
final_quote_session_mismatch
final_quote_session_not_regular
final_quote_spread_too_wide
final_quote_stale
final_quote_stale_after_path
final_quote_stale_at_return
final_quote_timestamp_in_future
final_quote_timestamp_in_future_after_handshake
final_quote_timestamp_in_future_after_path
final_quote_timestamp_in_future_at_return
final_quote_timestamp_missing
final_receipt_session_mismatch
final_receipt_session_mismatch_at_return
final_receipt_session_not_regular
final_receipt_session_not_regular_at_return
final_revalidation_exception
final_revalidation_failed
final_risk_invalid
final_round_limit_reached
final_scan_observation_in_future
final_scan_observation_missing
final_scan_observation_stale
final_scan_price_source_missing
final_snapshot_access_denied
final_snapshot_fetch_failed
final_snapshot_http_error
final_snapshot_other
final_snapshot_payload_invalid
final_snapshot_rate_limited
final_snapshot_unavailable
final_source_observation_in_future
final_source_observation_source_missing
final_source_observation_timestamp_missing
final_stock_revalidation_exception
final_stock_revalidation_failed
final_stop_and_tp1_touched_since_scan
final_stop_and_tp1_touched_since_trigger
final_stop_touched_since_scan
final_stop_touched_since_trigger
final_ticker_missing
final_tp1_touched_since_scan
final_tp1_touched_since_trigger
final_trade_levels_invalid
final_trade_levels_missing
final_watermark_invalid
fresh_5m_breakout_or_retest_missing
fresh_5m_state_missing_wait_retest
fresh_5m_state_missing_wait_trigger
fresh_5m_state_stale
fresh_closed_5m_trigger_missing
full_entry_model_not_tradeable
grade_below_alert_threshold
hard_extended_long_wait_retest
intraday_participation_too_low
intraday_structure_incomplete
intraday_structure_lost
intraday_trend_not_ready
intraday_unconfirmed_pattern
invalid_structure_plan
invalid_trade_geometry
invalid_trade_plan
latest_5m_green_reclaim
latest_5m_red_fade
listing_age_not_tradeable
listing_source_unknown
live_ask_too_far_above_trigger
live_entry_chased
live_executable_order_size_below_250_usd
live_net_risk_invalid
live_net_rr_below_minimum
live_net_tp1_rr_below_minimum
live_price_lost_breakout_confirmation
live_price_lost_retest_structure
live_quote_missing_or_stale
live_spread_missing_or_invalid
live_spread_too_wide
live_spread_unknown
live_trade_geometry_invalid
mail_adjacent_single_candidate_deferred
mail_adjacent_stock_revalidation_exception
mail_adjacent_stock_revalidation_failed
market_regime_red
market_regime_yellow
micro_trigger_missing
missing_cooldown_key
missing_current_drop
missing_entry_or_breakout_structure
missing_gmail_config
missing_recipient
missing_symbol
missing_ticker
momentum_mail_blocked_breakout_continuation_watch
momentum_mail_blocked_breakout_quality_low
momentum_mail_blocked_fakeout_risk
momentum_mail_blocked_late_intraday_chase
momentum_mail_blocked_late_session_without_daily_close
momentum_mail_blocked_missing_breakout_type
momentum_mail_blocked_missing_liquidity_history
momentum_mail_blocked_not_holding_upper_range
momentum_mail_blocked_range_not_near_breakout_high
momentum_mail_blocked_rvol_below_breakout_floor
momentum_mail_blocked_spike_rejected_from_high
momentum_mail_blocked_thin_baseline_liquidity
momentum_mail_blocked_tp1_already_touched_intraday
momentum_mail_blocked_trend_reclaim_not_breakout
momentum_mail_blocked_unknown_breakout_type
momentum_mail_blocked_upper_wick
near_binary_event
near_structural_barrier_wait_trigger
net_effective_rr_below_cost_adjusted_minimum
net_tp1_reward_below_cost_adjusted_minimum
new_listing_dump_watch_emails_disabled
no_crypto_execution_trigger
no_crypto_tradeable_signal
no_distinct_structural_tp2_at_acceptable_reward
no_fresh_trigger_or_ignition
no_high_confidence_overhead_structure_targets
no_new_listing_dump_watch_candidates
no_new_listing_signals
no_structural_stop_in_valid_risk_band
no_structural_tp1_at_acceptable_reward
no_verified_overhead_structure_targets
non_common_stock_product
not_a_premarket_row
not_active_short_signal
not_active_short_timing
not_closing_near_low
not_down_enough_for_breakdown
not_holding_highs_after_up_move
not_new_listing_dump
not_tradeable_signal_quality
open_equivalent_trade
orb_breakout_volume_unconfirmed
orb_completed_candle_stale
orb_completed_candle_unverified
orb_current_side_unverified
orb_current_breakout_lost
orb_invalid_target_geometry
orb_no_active_breakout
orb_not_tradeable
orb_range_break_stale
orb_recent_hold_weak
orb_tp1_already_reached
orb_waiting_for_entry_confirmation
outside_penny_price_band
outside_trigger_neighborhood
overhead_resistance_too_close
partial_crypto_data
penny_non_actionable
persistent_dedupe_active
planned_risk_invalid
premarket_extension_too_stretched
premarket_liquidity_below_threshold
premarket_missing_trade_levels
premarket_score_below_threshold
price_outside_penny_universe
projected_dollar_volume_below_3m
projected_dollar_volume_too_low
pump_continuation_risk
recent_dilution_reverse_split_or_company_risk_filing
regime_cooldown
risk_too_wide
row_claimed_by_parallel_sender
rr_below_alert_threshold
rvol_below_1_5
rvol_below_alert_threshold
rvol_below_bear_threshold
safety_not_ok
scanner_failed
score_below_alert_threshold
sec_filing_risk_data_unavailable
setup_quality_below_70
smtp_delivery_failed
smtp_delivery_outcome_unknown
spread_above_execution_limit
spread_too_wide_for_monitoring
startup_cooldown
stock_session_not_executable
stock_swing_mail_blocked_4h_extended_run
stock_swing_mail_blocked_4h_rejection
stock_swing_mail_blocked_low_volatility_budget
stock_swing_mail_blocked_missing_4h_state
stock_swing_mail_blocked_severe_business_risk
swing_4h_extended_run_wait_retest
swing_4h_rejection_wait_reclaim
swing_4h_state_missing_wait_trigger
swing_current_candle_fading
swing_day_move_exhausted_no_chase
swing_day_move_extended_wait_retest
swing_extended_wait_retest
swing_extended_without_volume_wait_retest
swing_gap_done_premarket_wait_retest
swing_gap_not_holding_open_wait_retest
swing_gap_not_holding_upper_range_wait_retest
swing_gap_wick_rejection_wait_retest
swing_hard_extended_no_chase
swing_momentum_breakout_quality_wait_retest
swing_momentum_not_holding_open_wait_retest
swing_momentum_not_holding_upper_range_wait_retest
swing_momentum_trend_reclaim_gap_wait_retest
swing_momentum_wick_rejection_wait_retest
swing_multi_day_exhausted_no_chase
swing_multi_day_extended_wait_retest
swing_not_holding_highs_after_move
swing_prevday_run_top_entry_wait_retest
swing_short_4h_state_missing_wait_trigger
swing_short_4h_wait_breakdown
swing_short_4h_wait_failed_reclaim
swing_short_bottom_entry_extended_wait_retest
swing_short_current_candle_reclaim
swing_short_day_move_exhausted_no_chase
swing_short_day_move_extended_wait_retest
swing_short_drop_extended_wait_failed_reclaim
swing_short_drop_too_extended_no_chase
swing_short_extended_wait_retest
swing_short_multi_day_exhausted_no_chase
swing_short_multi_day_extended_wait_retest
swing_short_not_closing_weak
swing_short_not_down_enough
swing_short_prevday_run_bottom_entry_wait_retest
swing_top_entry_extended_wait_retest
target_already_missed
technical_trigger_neighborhood
top_entry_extended_wait_retest
tracker_delivery_attempt_not_owned
tracker_delivery_contract_unavailable
tracker_delivery_intent_not_sendable
tracker_public_signal_plan_invalid
tracker_public_signal_ref_invalid
tracker_recipient_authorization_changed
trade_health_chase_risk
trade_health_fakeout_risk
trade_health_liquidity_risk
trade_health_no_trade
trade_health_score_below_80
trade_health_wait_for_continuation
trade_health_wait_for_retest
trade_health_wait_for_trigger
trade_health_watch_only
trade_rr_below_threshold
trade_score_below_80
trigger_stale_for_mail
turn_not_confirmed
unclassified_code_reason
unknown_trigger_rejection
weak_closed_bar_location
""".split())
MAIL_STATUSES = frozenset({"pending", "sending", "delivering", "uncertain", "sent", "expired", "dead"})
MAIL_CLASSES = frozenset({"trade", "swing_trade", "signal_update", "watch", "info"})
DELIVERY_JOURNAL_STATES = frozenset({"PENDING", "RECONCILED"})
MAIL_DB_SPECS = {
    "outbox": ("MAIL_OUTBOX_DB_PATH", "mail_outbox.sqlite"),
    "suppression": ("SUPPRESSION_TELEMETRY_DB_PATH", "suppression_telemetry.sqlite"),
}
EVIDENCE_MAX_ROWS = 100000
FIXED_SCANNER_CACHES = {
    "bear": "/tmp/bear_scanner_cache.json",
    "biotech": "/tmp/alpha_biotech_cache.json",
    "biotech_progress": "/tmp/alpha_biotech_progress.json",
    "turtle": "/tmp/turtle_scan_cache.json",
    "crypto_explosion": "/tmp/crypto_explosion_cache.json",
    "crypto_trade_signals": "/tmp/crypto_trade_signals_cache.json",
    "penny_stocks": "/tmp/penny_stock_scanner_cache.json",
    "penny_monitor": "/tmp/penny_stock_monitor_cache.json",
    "early_movers": "/tmp/early_movers_cache.json",
    "crash_monitor": "/tmp/crash_monitor_cache.json",
    "btc_divergenz": "/tmp/btc_divergenz_cache.json",
    "money_flow": "/tmp/money_flow_cache.json",
    "narrative_pulse": "/tmp/narrative_pulse_cache.json",
    "new_listing": "/tmp/new_listing_scanner.json",
    "volume_spikes": "/tmp/volume_spikes_cache.json",
    "orb": "/tmp/orb_scan_results.json",
    "market_context": "/tmp/market_context_cache.json",
}
STOCK_STRATEGY_CACHE_NAMES = (
    "momentum_breakout_long", "gap_momentum_long", "gap_momentum_short",
    "turtle_breakout", "bull_flag", "bear_flag", "compression_breakout",
    "cup_and_handle_breakout", "trend_reversal", "ma_bounce_long", "ma_bounce_short",
    "wyckoff_accumulation", "wyckoff_distribution",
)
STOCK_ATTEMPT_SLUGS = frozenset({
    "momentum_breakout_long", "gap_momentum_long", "gap_momentum_short", "cup_and_handle_breakout",
})
STOCK_ATTEMPT_ERROR_CODES = PUBLIC_SCAN_ERROR_CODES | frozenset({
    "scan_failed", "scan_timeout", "scan_already_running", "scan_cache_publish_failed", "scan_partial_cache",
})
STOCK_ATTEMPT_COUNTS = DIAGNOSTIC_COUNTS | frozenset({
    "strategies_total", "strategies_attempted", "strategies_completed", "strategies_failed", "current_result_count",
    "provider_requests", "history_cache_hits", "rate_wait_seconds", "elapsed_seconds", "leaf_elapsed_seconds",
    "timeout_retries_attempted", "timeout_retries_recovered",
})
STOCK_STAGE_TIMING_LABELS = frozenset({
    "history", "structure", "execution_history", "plan", "cache_publish", "special_filter",
})


def _project_stock_stage_timings(diagnostics, result):
    """Only bounded code-owned durations; never trust a source semantics label."""
    timings = _count_projection(diagnostics.get("stage_elapsed_ms"), STOCK_STAGE_TIMING_LABELS)
    if timings is not None:
        result["stage_elapsed_ms"] = timings
        result["stage_timing_semantics"] = "per_leaf_inclusive_elapsed_ms_not_additive"

def _iso_timestamp(value):
    """Canonical ISO time only; legacy naive server times stay explicitly naive."""
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError("Timestamp is not a bounded ISO string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("Timestamp is not ISO formatted") from None
    return parsed.isoformat()


def _nonnegative_count(value):
    return type(value) is int and 0 <= value <= 2**63 - 1


def _count_projection(value, allowed):
    if not isinstance(value, dict):
        return None
    result = {key: count for key, count in value.items()
              if key in allowed and _nonnegative_count(count)}
    omitted = len(value) - len(result)
    if omitted:
        # Keep evidence of unsupported schema without leaking free-form keys.
        result["_omitted_categories"] = omitted
    return result


def _scan_mail_audit_projection(value):
    # Mirrored protocol only: this root-invoked script never imports app code.
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return None
    result = {"schema_version": 1, "semantics": SCAN_MAIL_SEMANTICS}
    if type(value.get("candidate_rows")) is int and 0 <= value["candidate_rows"] <= 10**9:
        result["candidate_rows"] = value["candidate_rows"]
    for name, allowed in (("reason_occurrences", SUPPRESSION_REASONS), ("transport_events", SCAN_MAIL_EVENTS)):
        raw = value.get(name)
        result[name] = {key: raw[key] for key in sorted(allowed)
                        if isinstance(raw, dict) and type(raw.get(key)) is int and 0 <= raw[key] <= 10**9}
    return result


def _confluence_identity(value, expected_scanner):
    """Strict identifiers from the scanner call, not arbitrary source strings."""
    scanner = value.get("scanner")
    if (type(scanner) is not str or scanner not in ("bi_long", "bi_short")
            or (expected_scanner is not None and scanner != expected_scanner)
            or value.get("direction") != scanner.removeprefix("bi_")):
        return None
    run_id, revision = value.get("run_id"), value.get("code_revision")
    if type(run_id) is not str or re.fullmatch(r"[0-9a-f]{32}", run_id) is None:
        return None
    if (type(revision) is not str
            or re.fullmatch(r"(?:[0-9a-f]{12}(?:-dirty|-tree-unknown)?|unknown)", revision) is None
            or type(value.get("contract_version")) is not str
            or value["contract_version"] not in CONFLUENCE_CONTRACTS):
        return None
    try:
        started = datetime.fromisoformat(_iso_timestamp(value.get("started_at")))
        if started.tzinfo is None or started.utcoffset() is None:
            return None
        started_at = started.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        return None
    return {"scanner": scanner, "direction": scanner.removeprefix("bi_"),
            "run_id": run_id, "code_revision": revision,
            "contract_version": value["contract_version"], "started_at": started_at}


def _confluence_projection(value, expected_scanner=None):
    """Schema-1 numeric protocol only; absent/unknown fields never become zero."""
    if not isinstance(value, dict):
        return {"available": False, "schema_status": "invalid"}
    if (value.get("available") is False and value.get("reason") == "initialization_failed"
            and type(value.get("initialization_errors")) is int
            and value["initialization_errors"] == 1):
        return {"available": False, "reason": "initialization_failed", "initialization_errors": 1}
    if type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return {"available": False, "schema_status": "unknown"}
    if (type(value.get("required_green")) is not int or value["required_green"] != 17
            or any(not _nonnegative_count(value.get(key)) for key in CONFLUENCE_COUNTS)):
        return {"available": False, "schema_status": "invalid"}
    identity = _confluence_identity(value, expected_scanner)
    if identity is None:
        return {"available": False, "schema_status": "invalid_identity"}
    containers = {
        "green_count_histogram": frozenset(str(n) for n in range(21)),
        "available_count_histogram": frozenset(str(n) for n in range(21)),
        "bar_count_histogram": frozenset(str(n) for n in range(36, 51)) | {"other"},
        "first_hard_gate_counts": CONFLUENCE_HARD_GATES,
    }
    for key, allowed in containers.items():
        counts = value.get(key)
        if not isinstance(counts, dict) or any(not _nonnegative_count(counts.get(k)) for k in allowed):
            return {"available": False, "schema_status": "invalid"}
    factors = value.get("factor_counts")
    if not isinstance(factors, dict):
        return {"available": False, "schema_status": "invalid"}
    for key in CONFLUENCE_FACTORS:
        counts = factors.get(key)
        if (not isinstance(counts, dict)
                or any(not _nonnegative_count(counts.get(k)) for k in CONFLUENCE_FACTOR_COUNTS)):
            return {"available": False, "schema_status": "invalid"}
    result = {"available": True, "schema_version": 1, "required_green": 17}
    result.update(identity)
    result.update({key: value[key] for key in sorted(CONFLUENCE_COUNTS)})
    result.update({key: _count_projection(value[key], allowed) for key, allowed in containers.items()})
    result["factor_counts"] = {
        key: _count_projection(factors[key], CONFLUENCE_FACTOR_COUNTS)
        for key in sorted(CONFLUENCE_FACTORS)
    }
    if len(factors) > len(CONFLUENCE_FACTORS):
        result["omitted_factor_categories"] = len(factors) - len(CONFLUENCE_FACTORS)
    for name, allowed in {
        "failed_pair_counts": {f"{left:02}:{right:02}" for left in range(1, 21) for right in range(left + 1, 21)},
        "consolidation_days_histogram": {str(day) for day in range(51)} | {"other"},
    }.items():
        if name not in value:
            continue
        counts = value[name]
        if isinstance(counts, dict) and all(_nonnegative_count(counts.get(key)) for key in allowed):
            result[name] = _count_projection(counts, allowed)
        else:
            result.setdefault("invalid_optional_fields", []).append(name)
    return result


def tracker_snapshot(path):
    path = Path(path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("Tracker is not a regular file")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("BEGIN")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(signals)")}
        if not REQUIRED_COLUMNS <= columns:
            raise ValueError("Missing required tracker schema")
        inventory = dict(connection.execute(
            "SELECT COUNT(*) AS all_rows, "
            "COUNT(CASE WHEN mail_class='trade' THEN 1 END) AS trade_rows, "
            "COUNT(CASE WHEN mail_class='shadow' THEN 1 END) AS shadow_rows, "
            "COUNT(CASE WHEN mail_class IS NULL OR mail_class NOT IN ('trade','shadow') "
            "THEN 1 END) AS other_rows FROM signals"
        ).fetchone())
        if inventory["trade_rows"] > 100000:
            raise ValueError("Tracker exceeds bounded export size; no truncated success")
        selected = [name for name in TRACKER_COLUMNS if name in columns]
        rows = [dict(row) for row in connection.execute(
            "SELECT " + ",".join('"' + name + '"' for name in selected)
            + " FROM signals WHERE mail_class='trade' ORDER BY created_at,id"
        )]
    inventory["missing_report_columns"] = sorted(set(TRACKER_COLUMNS) - columns)
    return {"inventory": inventory, "rows": rows}


def service_pid(unit):
    # Fixed system utility, no shell, no repository hooks/config/code execution.
    value = subprocess.run(
        ["/usr/bin/systemctl", "show", unit, "--property=MainPID", "--value"],
        check=True, capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    pid = int(value)
    if pid <= 0:
        raise ValueError("Service inactive; writer path cannot be verified")
    return pid


class EvidenceUnavailable(ValueError):
    """Internal fixed reason only; SQLite errors and source values stay private."""


def _epoch(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 253402300799:
        raise EvidenceUnavailable("invalid_data")
    return value


@contextmanager
def _evidence_rows(path, table, columns, where="", parameters=(), *, text_limits=None):
    """One bounded, query-only transaction. No create/schema/migration helpers."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise EvidenceUnavailable("unsafe_file_type")
    with closing(sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=2)) as connection:
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        steps = [0]
        def budget():
            steps[0] += 1
            return int(steps[0] > 2000)
        connection.set_progress_handler(budget, 10000)
        connection.execute("BEGIN")
        kind = connection.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
        available = {row[1] for row in connection.execute('PRAGMA table_info("' + table + '")')}
        if kind != ("table",) or not set(columns) <= available:
            raise EvidenceUnavailable("unknown_schema")
        # Even malformed metadata cannot pull arbitrarily large text/BLOBs into
        # the collector. Numeric schema violations become unavailable, not 0.
        text_columns = {name: 100 for name in ("status", "mail_class", "scanner", "reason")}
        # Opt-in text fields preserve existing numeric projections (including
        # epoch columns). Limits and names come only from the collector code.
        for name, limit in (text_limits or {}).items():
            if name not in columns or type(limit) is not int or not 1 <= limit <= 100:
                raise EvidenceUnavailable("invalid_projection")
            text_columns[name] = limit
        selections = []
        for name in columns:
            quoted = '"' + name + '"'
            if name in text_columns:
                selections.append("CASE WHEN typeof(" + quoted + ")='text' AND length(" + quoted
                                  + ")<=" + str(text_columns[name]) + " THEN " + quoted + " ELSE NULL END")
            else:
                selections.append("CASE WHEN typeof(" + quoted + ") IN ('integer','real') THEN "
                                  + quoted + " ELSE NULL END")
        cursor = connection.execute("SELECT " + ",".join(selections)
                                    + ' FROM "' + table + '" ' + where + ' LIMIT ?',
                                    (*parameters, EVIDENCE_MAX_ROWS + 1))
        rows = cursor.fetchall()
        if len(rows) > EVIDENCE_MAX_ROWS:
            raise EvidenceUnavailable("row_limit_exceeded")
        yield [dict(zip(columns, row)) for row in rows]
        after = path.lstat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise EvidenceUnavailable("file_replaced")


def _unavailable_mail_error(error):
    if isinstance(error, FileNotFoundError):
        reason = "missing"
    elif isinstance(error, PermissionError):
        reason = "unreadable"
    elif isinstance(error, EvidenceUnavailable):
        reason = str(error)
    else:
        reason = "read_failed"
    return {"available": False, "reason": reason}


def outbox_snapshot(path, now):
    """Queue state is not inbox receipt evidence; no subject/body/recipient SELECT."""
    columns = ("status", "mail_class", "attempts", "created_at", "next_attempt_at", "expires_at", "sent_at")
    try:
        now = _epoch(now)
        by_status = {status: 0 for status in sorted(MAIL_STATUSES)}
        by_class = {name: 0 for name in sorted(MAIL_CLASSES)}
        result = {"available": True, "rows": 0, "by_status": by_status, "by_mail_class": by_class,
                  "unknown_status_rows": 0, "unknown_mail_class_rows": 0,
                  "created_last_24h": 0, "sent_last_24h": 0, "due_pending": 0,
                  "pending_past_expiry": 0, "stored_attempt_counter_sum": 0,
                  "max_stored_attempt_counter": 0, "oldest_open_created_at": None,
                  "latest_created_at": None, "latest_sent_at": None,
                  "coverage": "retry_and_uncertain_queue_not_all_mail",
                  "delivery_semantics": "queue_sent_is_not_recipient_receipt",
                  "fallback_uncertain_registry": "not_collected"}
        with _evidence_rows(path, "mail_outbox", columns) as rows:
            for row in rows:
                created, due, expires = (_epoch(row[key]) for key in ("created_at", "next_attempt_at", "expires_at"))
                sent = None if row["sent_at"] is None else _epoch(row["sent_at"])
                if not _nonnegative_count(row["attempts"]):
                    raise EvidenceUnavailable("invalid_data")
                result["rows"] += 1
                status, mail_class = row["status"], row["mail_class"]
                if type(status) is str and status in MAIL_STATUSES:
                    by_status[status] += 1
                else:
                    result["unknown_status_rows"] += 1
                if type(mail_class) is str and mail_class in MAIL_CLASSES:
                    by_class[mail_class] += 1
                else:
                    result["unknown_mail_class_rows"] += 1
                result["created_last_24h"] += int(now - 86400 <= created <= now)
                result["sent_last_24h"] += int(status == "sent" and sent is not None and now - 86400 <= sent <= now)
                result["due_pending"] += int(status == "pending" and due <= now <= expires)
                result["pending_past_expiry"] += int(status == "pending" and expires < now)
                result["stored_attempt_counter_sum"] += row["attempts"]
                result["max_stored_attempt_counter"] = max(result["max_stored_attempt_counter"], row["attempts"])
                if status in ("pending", "sending", "delivering", "uncertain"):
                    previous = result["oldest_open_created_at"]
                    result["oldest_open_created_at"] = created if previous is None else min(created, previous)
                result["latest_created_at"] = max(created, result["latest_created_at"] or 0)
                if sent is not None:
                    result["latest_sent_at"] = max(sent, result["latest_sent_at"] or 0)
        return result
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        return _unavailable_mail_error(error)


def suppression_snapshot(path, now):
    """24h-overlapping hourly buckets: occurrences, not unique candidates/mails."""
    columns = ("bucket_start", "scanner", "reason", "first_seen_at", "last_seen_at", "event_count")
    try:
        now = _epoch(now)
        start = int((now - 86400) // 3600) * 3600
        result = {"available": True, "window_start": now - 86400, "window_end": now,
                  "first_included_bucket_start": start,
                  "window_semantics": "overlapping_hour_buckets_may_include_up_to_1h_before_window",
                  "count_unit": "reason_occurrences_not_unique_signals_or_mails",
                  "reason_occurrences": 0, "unknown_dimension_occurrences": 0,
                  "by_scanner_reason": [], "latest_seen_at": None,
                  "dropped_write_journals": "not_collected"}
        counts = {}
        with _evidence_rows(path, "suppression_buckets", columns,
                            "WHERE bucket_start >= ? AND bucket_start <= ?", (start, now)) as rows:
            for row in rows:
                bucket, first, last = (_epoch(row[key]) for key in ("bucket_start", "first_seen_at", "last_seen_at"))
                if (type(row["bucket_start"]) is not int or bucket % 3600 != 0
                        or not bucket <= first <= last < bucket + 3600
                        or not _nonnegative_count(row["event_count"])):
                    raise EvidenceUnavailable("invalid_data")
                if not start <= bucket <= now or first > now:
                    continue
                count = row["event_count"]
                result["reason_occurrences"] += count
                result["latest_seen_at"] = max(last, result["latest_seen_at"] or 0)
                scanner, reason = row["scanner"], row["reason"]
                if (type(scanner) is not str or scanner not in SUPPRESSION_SCANNERS
                        or type(reason) is not str or reason not in SUPPRESSION_REASONS):
                    result["unknown_dimension_occurrences"] += count
                    continue
                key = (scanner, reason)
                counts[key] = counts.get(key, 0) + count
        result["by_scanner_reason"] = [
            {"scanner": scanner, "reason": reason, "reason_occurrences": count}
            for (scanner, reason), count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        ]
        return result
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        return _unavailable_mail_error(error)


def delivery_journal_snapshot(path, now):
    """Acceptance metadata per intent, not recipient validity or inbox receipt.

    Replays merge recipient cohorts and preserve the earliest accepted_at.
    PENDING is recorded reconciliation backlog, not failed SMTP; RECONCILED is
    a prior activation marker, not current tracker state. Retries are recorded
    reconciliation failures, not SMTP attempts. No private key/error is read.
    """
    try:
        now = _epoch(now)
        result = {"available": True, "rows": 0,
                  "by_state": {state: 0 for state in sorted(DELIVERY_JOURNAL_STATES)},
                  "unknown_state_rows": 0, "first_accepted_last_24h": 0,
                  "future_accepted_rows": 0, "oldest_pending_accepted_at": None,
                  "latest_accepted_at": None, "stored_reconciliation_retry_count_sum": 0,
                  "max_stored_reconciliation_retry_count": 0,
                  "coverage": "independent_acceptance_journal_metadata_only",
                  "count_unit": "intent_rows_not_messages_recipients_or_smtp_attempts",
                  "window_semantics": "earliest_recorded_acceptance_per_intent_in_inclusive_24h_window",
                  "timestamp_semantics": "utc_epoch_seconds_not_after_collection_time",
                  "delivery_semantics": "smtp_acceptance_metadata_not_inbox_receipt",
                  "recipient_cohort_validation": "not_collected"}
        with _evidence_rows(path, "delivery_acceptance_journal", ("state", "accepted_at", "retry_count"),
                            text_limits={"state": 100, "accepted_at": 64}) as rows:
            for row in rows:
                try:
                    accepted = datetime.fromisoformat(_iso_timestamp(row["accepted_at"]))
                    if accepted.tzinfo is None or accepted.utcoffset() is None:
                        raise ValueError("Acceptance time must include timezone")
                    accepted = _epoch(accepted.astimezone(timezone.utc).timestamp())
                except (ValueError, TypeError, OverflowError):
                    raise EvidenceUnavailable("invalid_data") from None
                retries = row["retry_count"]
                if not _nonnegative_count(retries):
                    raise EvidenceUnavailable("invalid_data")
                result["rows"] += 1
                state = row["state"]
                if type(state) is str and state in DELIVERY_JOURNAL_STATES:
                    result["by_state"][state] += 1
                else:
                    result["unknown_state_rows"] += 1
                result["stored_reconciliation_retry_count_sum"] += retries
                result["max_stored_reconciliation_retry_count"] = max(
                    result["max_stored_reconciliation_retry_count"], retries)
                if accepted > now:
                    result["future_accepted_rows"] += 1
                    continue
                result["first_accepted_last_24h"] += int(now - 86400 <= accepted)
                latest = result["latest_accepted_at"]
                result["latest_accepted_at"] = accepted if latest is None else max(latest, accepted)
                if state == "PENDING":
                    oldest = result["oldest_pending_accepted_at"]
                    result["oldest_pending_accepted_at"] = accepted if oldest is None else min(oldest, accepted)
        return result
    except (OSError, ValueError, TypeError, sqlite3.Error) as error:
        return _unavailable_mail_error(error)


def mail_store_evidence(runtimes, now):
    """Require matching writer routes AND inodes, including PrivateTmp mounts."""
    result = {}
    for store, snapshot in (("outbox", outbox_snapshot), ("suppression", suppression_snapshot),
                            ("delivery_journal", delivery_journal_snapshot)):
        try:
            paths = [runtime.get("_mail_paths", {}).get(store) for runtime in runtimes.values()]
            if any(path is None for path in paths):
                result[store] = {"available": False, "reason": "unverified_runtime_path"}
                continue
            if len(set(paths)) != 1:
                result[store] = {"available": False, "reason": "writer_path_mismatch"}
                continue
            targets = [_namespace_path(runtime, path) for runtime, path in zip(runtimes.values(), paths)]
            identities = [target.lstat() for target in targets]
            if not all(stat.S_ISREG(identity.st_mode) for identity in identities):
                raise EvidenceUnavailable("unsafe_file_type")
            if len({(identity.st_dev, identity.st_ino) for identity in identities}) != 1:
                result[store] = {"available": False, "reason": "writer_file_mismatch"}
                continue
            result[store] = snapshot(targets[0], now)
            if any((target.lstat().st_dev, target.lstat().st_ino) != (identity.st_dev, identity.st_ino)
                   for target, identity in zip(targets, identities)):
                raise EvidenceUnavailable("file_replaced")
        except (OSError, ValueError, TypeError) as error:
            result[store] = _unavailable_mail_error(error)
    return result


def _proc_directory(pid):
    return Path("/proc") / str(pid)


def _namespace_path(runtime, service_path):
    """Keep /proc/PID/root intact: resolve() would escape a PrivateTmp view."""
    target = PurePosixPath(str(service_path))
    if not target.is_absolute() or ".." in target.parts:
        raise ValueError("Expected an absolute service path without parent traversal")
    return Path(runtime["process_root"]).joinpath(*target.parts[1:])


def runtime_identity(unit, app):
    pid = service_pid(unit)
    proc = _proc_directory(pid)
    cwd = (proc / "cwd").resolve(strict=True)
    if cwd != app:
        raise ValueError("Unexpected service working directory")
    ownership = {}
    for line in (proc / "status").read_text(encoding="ascii").splitlines():
        key, separator, values = line.partition(":")
        if separator and key in {"Uid", "Gid"}:
            ids = [int(value) for value in values.split()]
            if len(ids) != 4 or len(set(ids)) != 1:
                raise ValueError("Mixed process privilege identity")
            ownership[key] = ids[0]
    if set(ownership) != {"Uid", "Gid"}:
        raise ValueError("Missing process privilege identity")
    # Inspect only needed paths; never print or return raw environment bytes.
    environment = {}
    for item in (proc / "environ").read_bytes().split(b"\0"):
        key, separator, value = item.partition(b"=")
        name = key.decode("ascii", errors="ignore")
        if separator and name in PATH_ENV:
            environment[name] = value.decode("utf-8", errors="strict")
    data = Path(environment.get("ALPHA_DATA_DIR", str(app / "data_cache")))
    if not data.is_absolute():
        data = cwd / data
    tracker = Path(environment.get("SIGNAL_TRACKER_DB_PATH", str(data / "signal_tracker.sqlite")))
    if not tracker.is_absolute():
        tracker = cwd / tracker
    mail_paths = {}
    for name, (override, filename) in MAIL_DB_SPECS.items():
        path = Path(environment.get(override, str(data / filename)))
        if not path.is_absolute():
            path = cwd / path
        # Parent traversal after a symlink is not equivalent to lexical
        # normalization. Do not read a plausible but different host/namespace
        # file; leave such routes explicitly unverified without resolving.
        mail_paths[name] = None if ".." in path.parts else os.path.abspath(str(path))
    # Match _delivery_journal_path without importing application code. Derive
    # from the configured tracker route before host resolution: a symlink or
    # PrivateTmp route can have a different sibling than the resolved target.
    journal_override = environment.get("SIGNAL_DELIVERY_JOURNAL_DB_PATH", "").strip()
    journal = Path(journal_override) if journal_override else tracker.with_name(
        tracker.stem + "_delivery_acceptance" + (tracker.suffix or ".sqlite"))
    if not journal.is_absolute():
        journal = cwd / journal
    mail_paths["delivery_journal"] = None if ".." in journal.parts else os.path.abspath(str(journal))
    temp = PurePosixPath(environment.get("ALPHA_RUNTIME_TMP_DIR") or "/tmp")
    if not temp.is_absolute():
        temp = PurePosixPath(cwd.as_posix()) / temp
    # This intentional kernel-provided symlink is retained, not resolved to the
    # host root. Cache paths below it use the verified service's mount namespace.
    process_root = proc / "root"
    identity = {"pid": pid, "uid": ownership["Uid"], "gid": ownership["Gid"],
            "cwd": str(cwd), "tracker": str(tracker.resolve(strict=True)),
            "process_root": str(process_root), "progress_dir": str(temp),
            "_mail_paths": mail_paths}
    if not _namespace_path(identity, temp).is_dir():
        raise ValueError("Service progress directory is unavailable")
    return identity


def drop_reader_privileges(uid, gid, os_api=None):
    """Irreversible Linux UID/GID drop before SQLite can open WAL/SHM files."""
    if type(uid) is not int or type(gid) is not int or uid <= 0 or gid <= 0:
        raise ValueError("Reader must be a non-root service account")
    if os_api is None:
        import os as os_api
    if os_api.geteuid() == 0:
        os_api.setgroups([])
        os_api.setresgid(gid, gid, gid)
        os_api.setresuid(uid, uid, uid)
        if os_api.getgroups():
            raise ValueError("Supplementary root groups survived drop")
    if os_api.getresuid() != (uid, uid, uid) or os_api.getresgid() != (gid, gid, gid):
        raise ValueError("Reader privilege identity mismatch")


def local_health():
    # Fixed loopback destination, no environment proxies or HTTP redirects.
    connection = http.client.HTTPConnection("127.0.0.1", 8000, timeout=10)
    try:
        connection.request("GET", "/api/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("Health did not return HTTP200")
        data = response.read(16385)
        if len(data) > 16384:
            raise ValueError("Health response too large")
        payload = json.loads(data)
        if not isinstance(payload, dict) or payload.get("status") != "healthy":
            raise ValueError("API is not healthy")
        health = {"status": "healthy", "timestamp": _iso_timestamp(payload.get("timestamp"))}
        for key in ("revision", "frontend_bundle"):
            value = payload.get(key)
            if not isinstance(value, str) or not re.fullmatch(r"(?:[0-9a-f]{12}|unknown)", value):
                raise ValueError("Health identity is not a revision identifier")
            health[key] = value
        return health
    finally:
        connection.close()


class _CacheReadError(ValueError):
    """Code-owned cache failure category, never an OS/provider error message."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _read_cache_payload(path):
    """Bounded, read-only regular-file read; never wait on an exchanged FIFO."""
    path = Path(path)
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise _CacheReadError("not_regular")
    if before.st_size > CACHE_MAX_BYTES:
        raise _CacheReadError("too_large")
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_BINARY", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if (not stat.S_ISREG(opened.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)):
            raise _CacheReadError("changed_during_read")
        if opened.st_size > CACHE_MAX_BYTES:
            raise _CacheReadError("too_large")
        raw = stream.read(CACHE_MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        if ((opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
            raise _CacheReadError("changed_during_read")
    if len(raw) > CACHE_MAX_BYTES:
        raise _CacheReadError("too_large")
    return json.loads(raw.decode("utf-8"))


def _crypto_row_state_counts(rows):
    """Count stored protocol states; these are not fresh trade/mail approvals."""
    counts = {"semantics": "cached_rows_not_live_confirmation_or_delivery", "invalid_rows": 0}
    fields = (*CRYPTO_ROW_STATES, "execution_trigger_ok", "micro_trigger_ok")
    counts.update({field: {} for field in fields})
    for row in rows:
        if not isinstance(row, dict):
            counts["invalid_rows"] += 1
            continue
        for field in fields:
            value = row.get(field)
            if field not in row:
                category = "_missing"
            elif field in CRYPTO_ROW_STATES:
                category = value if type(value) is str and value in CRYPTO_ROW_STATES[field] else "_unrecognized"
            else:
                category = str(value).lower() if type(value) is bool else "_unrecognized"
            counts[field][category] = counts[field].get(category, 0) + 1
    return counts


def safe_cache_summary(path):
    """Counts/numeric funnel only, not cached tickers or free-form messages."""
    try:
        payload = _read_cache_payload(path)
        if not isinstance(payload, dict):
            return {"available": False, "reason": "invalid_payload"}
        result = {"available": True}
        for key in ("checked", "total", "hits", "no_data", "count"):
            value = payload.get(key)
            if _nonnegative_count(value):
                result[key] = value
        if type(payload.get("partial")) is bool:
            result["partial"] = payload["partial"]
        stamp = payload.get("timestamp")
        if type(stamp) in (int, float) and 0 <= stamp <= 253402300799 and math.isfinite(stamp):
            result["timestamp"] = stamp
        if "cached_at" in payload:
            try:
                result["cached_at"] = _iso_timestamp(payload["cached_at"])
            except (ValueError, TypeError):
                result["cached_at"] = None
        status = payload.get("status")
        if isinstance(status, str) and status in CACHE_STATUSES:
            result["status"] = status
        elif "status" in payload:
            result["status"] = "unknown"
        detail = payload.get("detail")
        if status == "error" and type(detail) is str and detail in PUBLIC_SCAN_ERROR_CODES:
            result["error_code"] = detail
        rows = payload.get("results")
        result["raw_rows"] = len(rows) if isinstance(rows, list) else None
        if isinstance(rows, list) and Path(path).name in CRYPTO_CACHE_NAMES:
            result["row_state_counts"] = _crypto_row_state_counts(rows)
        scan_stats = payload.get("scan_stats")
        if isinstance(scan_stats, dict):
            projected = {"numeric_counts": {
                key: value for key, value in scan_stats.items()
                if key in CRYPTO_SCAN_COUNTS and _nonnegative_count(value)
            }}
            for key in ("source_degraded", "incomplete"):
                if type(scan_stats.get(key)) is bool:
                    projected[key] = scan_stats[key]
            exchange_counts = _count_projection(scan_stats.get("by_exchange"), CRYPTO_VENUES)
            if exchange_counts is not None:
                projected["by_exchange"] = exchange_counts
            result["scan_stats"] = projected
        diagnostics = payload.get("diagnostics") or {}
        if isinstance(diagnostics, dict):
            _project_stock_stage_timings(diagnostics, result)
            result["numeric_diagnostics"] = {
                key: value for key, value in diagnostics.items()
                if key in DIAGNOSTIC_COUNTS and _nonnegative_count(value)
            }
            if diagnostics.get("coverage") in ("complete", "incomplete"):
                result["coverage"] = diagnostics["coverage"]
            if type(diagnostics.get("scan_in_progress")) is bool:
                result["scan_in_progress"] = diagnostics["scan_in_progress"]
            reason = diagnostics.get("data_error_reason")
            if type(reason) is str and reason in DATA_ERROR_REASONS:
                result["data_error_reason"] = reason
            transport_reason = diagnostics.get("transport_error_reason")
            if type(transport_reason) is str and transport_reason in TRANSPORT_ERROR_REASONS:
                result["transport_error_reason"] = transport_reason
            for name, allowed in (("data_error_counts", DATA_ERROR_REASONS),
                                  ("transport_error_counts", TRANSPORT_ERROR_REASONS),
                                  ("data_error_value_classes", DATA_ERROR_VALUE_CLASSES),
                                  ("data_error_positions", DATA_ERROR_POSITIONS),
                                  ("data_error_fields", frozenset({"t", "o", "h", "l", "c", "v", "bar", "unknown"}))):
                counts = _count_projection(diagnostics.get(name), allowed)
                if counts is not None:
                    result[name] = counts
            if "run_as_of" in diagnostics:
                try:
                    instant = datetime.fromisoformat(_iso_timestamp(diagnostics["run_as_of"]))
                    if instant.tzinfo is None or instant.utcoffset() is None:
                        raise ValueError("naive as-of")
                    result["run_as_of"] = instant.astimezone(timezone.utc).isoformat()
                except (ValueError, TypeError, OverflowError):
                    result["run_as_of_available"] = False
            dates = diagnostics.get("analysis_session_dates")
            if isinstance(dates, dict):
                allowed = {"other"}
                for key in dates:
                    if type(key) is str and re.fullmatch(r"\d{4}-\d{2}-\d{2}", key):
                        try:
                            datetime.strptime(key, "%Y-%m-%d")
                            allowed.add(key)
                        except ValueError:
                            pass
                if len(allowed) <= 5:
                    result["analysis_session_dates"] = _count_projection(dates, allowed)
                else:
                    result["analysis_session_dates_available"] = False
            for key, allowed in (("rejected", REJECTION_CODES), ("stage_counts", STAGE_COUNTS),
                                 ("data_failures", DATA_FAILURE_CODES), ("legitimate_filters", REJECTION_CODES)):
                counts = _count_projection(diagnostics.get(key), allowed)
                if counts is not None:
                    result[key] = counts
            cup_counts = _count_projection(diagnostics.get("cup_terminal_counts"), CUP_TERMINAL_REASONS)
            if cup_counts is not None:
                result["cup_terminal_counts"] = cup_counts
                result["cup_terminal_count_semantics"] = CUP_TERMINAL_COUNT_SEMANTICS
            plan_counts = _count_projection(diagnostics.get("plan_build_counts"), PLAN_BUILD_CODES)
            wyckoff_counts = _count_projection(diagnostics.get("wyckoff_reasons"), WYCKOFF_REASONS)
            if wyckoff_counts is not None:
                result["wyckoff_reasons"] = wyckoff_counts
            if plan_counts is not None:
                result["plan_build_counts"] = plan_counts
                result["plan_build_count_semantics"] = "builder_outcomes_not_final_eligibility_or_delivery"
            if "confluence" in diagnostics:
                result["confluence"] = _confluence_projection(
                    diagnostics["confluence"], BI_CACHE_SCANNERS.get(Path(path).name))
        return result
    except FileNotFoundError:
        return {"available": False, "reason": "missing"}
    except _CacheReadError as exc:
        return {"available": False, "reason": exc.reason}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {"available": False, "reason": "invalid_json"}
    except OSError:
        return {"available": False, "reason": "unreadable"}
    except (ValueError, TypeError):
        return {"available": False, "reason": "invalid_payload"}


def _attempt_timestamp(value):
    parsed = datetime.fromisoformat(_iso_timestamp(value))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Attempt time has no timezone")
    return parsed.astimezone(timezone.utc).isoformat()


def _attempt_result(value):
    """A failed/in-progress attempt cannot claim a completed zero-result scan."""
    status, count, code = value.get("status"), value.get("result_count"), value.get("error_code")
    if type(status) is not str or status not in {"running", "complete", "error"}:
        raise ValueError("Unknown attempt status")
    if status == "complete":
        if not _nonnegative_count(count) or code not in (None, ""):
            raise ValueError("Invalid completed attempt")
    elif count is not None:
        raise ValueError("Incomplete attempt has final result count")
    if status == "error":
        if type(code) is not str or code not in STOCK_ATTEMPT_ERROR_CODES:
            raise ValueError("Unknown attempt error code")
    elif code not in (None, ""):
        raise ValueError("Non-error attempt contains an error code")
    return {"status": status, "result_count": count, "error_code": code or None}


def safe_strategy_attempt_summary(path, expected_slug):
    """Optional schema-1 attempt metadata only; no status API or app import."""
    try:
        payload = _read_cache_payload(path)
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int or payload["schema_version"] != 1:
            return {"available": False, "reason": "unknown_schema"}
        is_sweep = expected_slug == "stock_strategy_sweep"
        if (expected_slug not in STOCK_ATTEMPT_SLUGS and not is_sweep
                or payload.get("strategy_slug") != expected_slug
                or payload.get("attempt_kind") != ("stock_strategy_sweep" if is_sweep else "stock_strategy")
                or payload.get("results") != []):
            raise ValueError("Invalid attempt identity")
        run_id, revision = payload.get("run_id"), payload.get("code_revision")
        if (type(run_id) is not str or re.fullmatch(r"[0-9a-f]{32}", run_id) is None
                or type(revision) is not str
                or re.fullmatch(r"(?:[0-9a-f]{12}(?:-dirty|-tree-unknown)?|unknown)", revision) is None):
            raise ValueError("Invalid attempt revision")
        started, updated = (_attempt_timestamp(payload.get(name)) for name in ("started_at", "updated_at"))
        if datetime.fromisoformat(updated) < datetime.fromisoformat(started):
            raise ValueError("Attempt updated before start")
        result = {"available": True, "schema_version": 1, "strategy_slug": expected_slug,
                  "attempt_kind": "stock_strategy_sweep" if is_sweep else "stock_strategy",
                  "run_id": run_id, "code_revision": revision, "started_at": started, "updated_at": updated}
        result.update(_attempt_result(payload))
        diagnostics = payload.get("diagnostics")
        if not isinstance(diagnostics, dict):
            raise ValueError("Missing attempt diagnostics")
        _project_stock_stage_timings(diagnostics, result)
        result["numeric_diagnostics"] = {
            key: value for key, value in diagnostics.items()
            if key in STOCK_ATTEMPT_COUNTS and _nonnegative_count(value)
        }
        if diagnostics.get("coverage") in ("complete", "incomplete"):
            result["coverage"] = diagnostics["coverage"]
        mail_audit = _scan_mail_audit_projection(diagnostics.get("mail_audit"))
        if mail_audit is not None:
            result["mail_audit"] = mail_audit
        if diagnostics.get("runtime_phase") in (
            "starting", "universe", "history", "analyzing", "special_filter", "enrichment",
            "publish", "mail_guard", "work_timeout", "error", "complete",
        ):
            result["runtime_phase"] = diagnostics["runtime_phase"]
        for key, allowed in (("stage_counts", STAGE_COUNTS), ("rejected", REJECTION_CODES),
                             ("data_failures", DATA_FAILURE_CODES), ("legitimate_filters", REJECTION_CODES)):
            counts = _count_projection(diagnostics.get(key), allowed)
            if counts is not None:
                result[key] = counts
        plan_counts = _count_projection(diagnostics.get("plan_build_counts"), PLAN_BUILD_CODES)
        if plan_counts is not None:
            result["plan_build_counts"] = plan_counts
            result["plan_build_count_semantics"] = "builder_outcomes_not_final_eligibility_or_delivery"
        cup_counts = _count_projection(diagnostics.get("cup_terminal_counts"), CUP_TERMINAL_REASONS)
        if cup_counts is not None:
            result["cup_terminal_counts"] = cup_counts
            result["cup_terminal_count_semantics"] = CUP_TERMINAL_COUNT_SEMANTICS
        if is_sweep:
            children = diagnostics.get("strategy_results")
            if not isinstance(children, dict):
                raise ValueError("Missing sweep child outcomes")
            result["strategy_results"] = {}
            for slug, child in children.items():
                if slug not in STOCK_ATTEMPT_SLUGS:
                    result["omitted_strategy_categories"] = result.get("omitted_strategy_categories", 0) + 1
                    continue
                if not isinstance(child, dict) or child.get("status") not in ("complete", "error"):
                    raise ValueError("Invalid sweep child outcome")
                projected = _attempt_result(child)
                if "timeout_retry_count" in child or "initial_error_code" in child:
                    if (type(child.get("timeout_retry_count")) is not int
                            or child["timeout_retry_count"] != 1
                            or child.get("initial_error_code") != "scan_timeout"):
                        raise ValueError("Invalid bounded timeout retry outcome")
                    projected.update(timeout_retry_count=1, initial_error_code="scan_timeout")
                if child["status"] == "complete" or "aggregate_candidate_count" in child:
                    count = child.get("aggregate_candidate_count")
                    if not _nonnegative_count(count) and not (child["status"] == "error" and count is None):
                        raise ValueError("Invalid aggregate candidate count")
                    projected["aggregate_candidate_count"] = count
                result["strategy_results"][slug] = projected
            mail_status, mail_code = diagnostics.get("mail_status"), diagnostics.get("mail_error_code")
            if type(mail_status) is not str or mail_status not in {"not_attempted", "guarded", "no_results", "error"}:
                raise ValueError("Unknown guarded mail state")
            if mail_status == "error":
                if type(mail_code) is not str or mail_code not in STOCK_ATTEMPT_ERROR_CODES:
                    raise ValueError("Unknown guarded mail error")
            elif mail_code not in (None, ""):
                raise ValueError("Non-error mail state contains error")
            result["mail_status"] = mail_status
            result["mail_error_code"] = mail_code or None
            result["mail_status_semantics"] = "guard_execution_not_delivery_evidence"
        return result
    except FileNotFoundError:
        return {"available": False, "reason": "missing"}
    except (OSError, ValueError, TypeError, OverflowError):
        return {"available": False, "reason": "invalid_or_unreadable"}


def collect(app):
    app = Path(app).resolve(strict=True)
    units = ("tradingbot-api.service", "tradingbot-bg.service")
    runtimes = {unit: runtime_identity(unit, app) for unit in units}
    if len({item["tracker"] for item in runtimes.values()}) != 1:
        raise ValueError("API/BG use different trackers; no misleading combined export")
    import pwd
    service_account = pwd.getpwnam("tradingbot")
    if any((runtime["uid"], runtime["gid"]) != (service_account.pw_uid, service_account.pw_gid)
           for runtime in runtimes.values()):
        raise ValueError("Writers do not use the expected service identity")
    # SQLite readers may need WAL shared-memory sidecars. They must never be
    # created as root in the service-owned tree; immutable=1 is NOT safe for WAL.
    drop_reader_privileges(service_account.pw_uid, service_account.pw_gid)
    health = local_health()
    tracker = tracker_snapshot(runtimes[units[0]]["tracker"])
    api_runtime = runtimes[units[0]]
    cache_paths = {
        "bi_long": "/tmp/bi_cache_long.json", "bi_short": "/tmp/bi_cache_short.json",
        "momentum": "/tmp/strategy_momentum_breakout_long_cache.json",
        "bi_long_progress": PurePosixPath(api_runtime["progress_dir"]) / "bi_scan_progress_long.json",
        "bi_short_progress": PurePosixPath(api_runtime["progress_dir"]) / "bi_scan_progress_short.json",
        **FIXED_SCANNER_CACHES,
    }
    for name in STOCK_STRATEGY_CACHE_NAMES:
        if name != "momentum_breakout_long":
            cache_paths["stock_strategy_" + name] = "/tmp/strategy_" + name + "_cache.json"
    caches = {name: safe_cache_summary(_namespace_path(api_runtime, path)) for name, path in cache_paths.items()}
    attempts = {slug: safe_strategy_attempt_summary(
        _namespace_path(api_runtime, PurePosixPath(api_runtime["progress_dir"]) /
                        ("stock_strategy_" + ("sweep" if slug == "stock_strategy_sweep" else slug) + "_attempt.json")), slug)
        for slug in sorted(STOCK_ATTEMPT_SLUGS | {"stock_strategy_sweep"})}
    mail = mail_store_evidence(runtimes, datetime.now(timezone.utc).timestamp())
    if any(runtime_identity(unit, app) != runtimes[unit] for unit in units):
        raise ValueError("Writer restarted or paths changed during collection")
    return {"schema_version": 1, "kind": "private_server_evidence",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
            "runtime": {unit: {key: value for key, value in runtime.items() if key != "_mail_paths"}
                        for unit, runtime in runtimes.items()}, "health": health,
            "tracker": tracker, "scanner_caches": caches,
            "stock_strategy_attempts": attempts,
            "mail_evidence": mail,
            "scanner_attempt_coverage": {
                "bi": "persisted_progress_included",
                "stock_strategies": "four_auto_leaf_and_sweep_attempt_files_included_if_available",
                "other_scanners": "persisted_final_caches_only_ram_attempts_not_collected",
                "status_endpoint": "not_called_because_not_proven_read_only",
            },
            "notes": ["Tracker rows are one SQLite read transaction including WAL.",
                      "SQLite/cache reading runs as verified non-root tradingbot UID/GID.",
                      "Cache/progress paths use the verified API process mount namespace.",
                      "Normal SQLite WAL/SHM coordination is possible; no root-owned sidecars.",
                      "Cache/health files are separately observed, not an atomic cross-file snapshot.",
                      "Outbox, suppression and delivery journal are separate query-only SQLite transactions; missing is not zero.",
                      "Suppression counts overlap and are not unique signals/mails; dropped-write journals are not read.",
                      "Outbox is a retry/uncertainty queue, not all mail sends; sent is not inbox receipt.",
                      "Delivery journal counts are intent metadata, not recipient-cohort validation or inbox receipt.",
                      "Journal acceptance windows use earliest acceptance per intent, not mail sends during the window.",
                      "Journal PENDING and retry counters concern tracker reconciliation, not SMTP failures or attempts.",
                      "Outbox fallback uncertainty and fallback tracker-acceptance registries are not read.",
                      "Attempt files are best effort: unavailable is unknown; guarded mail status is not delivery proof.",
                      "No recipients, account blobs, mail bodies or API keys selected.",
                      "No broker fills/cost ledger; price-path results are not net account PnL.",
                      "Keep this projected row export private; do not commit or publish it."]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", default="/home/tradingbot/app")
    args = parser.parse_args()
    try:
        result = collect(args.app)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False)
    except Exception:
        print("Evidence export failed: verify active services, writer paths and readable DB; nothing changed.", file=sys.stderr)
        return 2
    print(encoded)
    return 0


if __name__ == "__main__":
    sys.exit(main())
