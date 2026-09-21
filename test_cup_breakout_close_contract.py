"""Cup breakout proof must come from the close, never its upper wick."""
from copy import deepcopy

import pytest

import api
from test_cup_handle_scanner import _cup_handle_bars
from test_cup_handle_audit_fixes import _mk_candidate, _mock_mail_env
from modules.cup_signal_contract import CUP_PATTERN_CONTRACT_VERSION


@pytest.mark.parametrize("close", [100.8, 101.0, 101.2, 101.4])
def test_wick_above_rim_is_not_a_confirmed_daily_breakout(close):
    bars = _cup_handle_bars()
    # Valid OHLC, closes in upper quarter, high above both original rims.
    # The old OR branch accepted this even without a close above resistance.
    bars[-1].update(open=100.5, close=close, high=102.0, low=97.0)
    assert api._detect_cup_handle_breakout(bars, current_price=close) is None


def test_current_quote_does_not_replace_failed_confirmation_close():
    bars = _cup_handle_bars()
    bars[-1].update(open=100.5, close=101.0, high=102.0, low=97.0)
    assert api._detect_cup_handle_breakout(bars, current_price=102.0) is None


def test_valid_close_survives_and_evidence_keeps_original_close():
    bars = _cup_handle_bars()
    before = deepcopy(bars)
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None
    assert bars == before
    assert setup["cup_pattern_version"] == CUP_PATTERN_CONTRACT_VERSION
    assert setup["cup_confirmation_close"] == bars[-1]["close"]
    assert setup["cup_confirmation_level"] >= setup["cup_rim_level"]
    assert setup["cup_confirmation_close"] >= setup["cup_confirmation_level"] * 1.002


@pytest.mark.parametrize("bad", [True, 0, -1, float("nan"), float("inf"), "bad", {}])
def test_invalid_current_quote_cannot_mint_a_confirmation(bad):
    assert api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=bad) is None


@pytest.mark.parametrize("field", ["high", "low", "close"])
@pytest.mark.parametrize("bad", [0, float("nan"), float("inf")])
def test_bad_last_bar_is_not_dropped_to_promote_a_previous_bar(field, bad):
    bars = _cup_handle_bars()
    bars.append(dict(bars[-1], **{field: bad}))
    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


def _current_row(monkeypatch):
    _mock_mail_env(monkeypatch, allowed=False)
    row = api._apply_cup_handle_strategy_filter(_mk_candidate(), {"min_dollar_volume": 2_000_000})
    assert row and api._cup_signal_contract_valid(row)
    return row


def test_current_proof_survives_bounded_watch_roundtrip(monkeypatch):
    row = _current_row(monkeypatch)
    stored = api._cup_handle_watch_row(row)
    assert api._cup_signal_contract_valid(stored, strategy_name="Cup and Handle Breakout")
    for key in ("cup_pattern_version", "cup_rim_level", "cup_confirmation_level", "cup_confirmation_close"):
        assert stored[key] == row[key]


@pytest.mark.parametrize("mode", ["legacy", "below_close", "wrong_direction", "wrong_version"])
def test_bad_or_old_proof_cannot_promote_or_revalidate(monkeypatch, mode):
    row = _current_row(monkeypatch)
    if mode == "legacy":
        for key in list(row):
            if key.startswith("cup_"):
                row.pop(key)
    elif mode == "below_close":
        row["cup_confirmation_close"] = row["cup_rim_level"]
    elif mode == "wrong_direction":
        row["direction"] = "SHORT"
    else:
        row["cup_pattern_version"] = "cup_bowl_close_v1"
    assert api._promote_cup_handle_watch_row(row, {"trigger_observed_ts": api.time.time()}) is None
    assert not api._queue_cup_handle_next_session_watch(
        row, confirmation_date="2026-09-18", target_session_date="2026-09-21", breakout_level=101.2,
    )
    def forbidden(*args, **kwargs):
        raise AssertionError("rejected Cup must not fetch live data")
    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", forbidden)
    monkeypatch.setattr(api, "_stock_swing_delayed_observation", forbidden)
    assert api._revalidate_stock_strategy_mail_candidate(row)["reason"].startswith("cup_contract_")
    assert api._revalidate_stock_swing_plan(row, now_ts=api.time.time(), scanner_name="stock_strategy")["reason"].startswith("cup_contract_")
    assert not api._scanner_row_is_trade_signal(row, "strategy_scan")


def test_nameless_legacy_cup_cannot_enter_mail_tracking_or_enrichment(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy Cup escaped the early gate")
    for name in ("_stock_trade_email_status", "_load_common_stock_universe", "_safe_record_alert_signals", "_send_email_alert"):
        monkeypatch.setattr(api, name, forbidden)
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    api._send_strategy_scan_alerts("Cup and Handle Breakout", [{"ticker": "LEGACY", "score": 99}], "stocks")
    assert suppressed[0][1] == {"cup_contract_legacy_or_missing_version": 1}
