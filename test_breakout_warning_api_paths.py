"""Producer-to-presentation contract; all scans/provider/mail calls are mocked."""
from copy import deepcopy

import pytest

import api
from modules.breakout_warnings import BREAKOUT_WITHOUT_RETEST_CODE as CODE
from modules.breakout_warnings import apply_breakout_warning, breakout_warning_fields
from modules import stock_swing_contract as swing
from test_cup_handle_audit_fixes import _mk_candidate, _mock_mail_env
from test_stock_momentum_confirmed_contract import _wrapper_fixture, NOW, NAME
from test_stock_starter_swing import payload


def _crypto_row(action):
    plan = {"entry": 10., "stop_loss": 9.5, "tp1": 10.8, "tp2": 11.6,
            "target_quality": "STRUCTURAL", "direction": "LONG"}
    return {**plan, "Price": 10., "trade_action": action, "trade_setup": dict(plan),
            "live_rr_ratio": 2.4, "distance_to_entry_r": 0.}


@pytest.mark.parametrize("action", ["LONG_TRIGGER", "WAIT_FOR_RETEST"])
def test_actual_crypto_breakout_is_alternative_to_retest(action):
    row = dict(_crypto_row(action), warning_codes=["independent"])
    api._apply_early_mover_signal_state(row, {
        "ok": True, "timeframe": "5m", "matched": ["breakout"],
        "reason": "adaptive_5m_breakout", "execution_score": 90,
    })
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["retest_confirmed"] is False
    assert row["retest_status"] == "not_confirmed"
    assert row["warning_codes"] == ["independent", CODE]
    assert row["trade_setup"]["warning_codes"] == [CODE]
    assert "Ruecktest" in api._format_alert_plan_html(row)


@pytest.mark.parametrize("trigger", [
    {"ok": False, "timeframe": "5m", "matched": ["breakout"], "reason": "thin_orderbook_market_impact"},
    {"ok": True, "timeframe": "1m", "matched": ["breakout"]},
    {"ok": True, "timeframe": "5m", "matched": ["vwap_reclaim"]},
])
def test_missing_or_wrong_trigger_does_not_get_warning_or_bypass(trigger):
    row = {"trade_action": "WAIT_FOR_RETEST", "trade_setup": {}}
    apply_breakout_warning(row, True)
    apply_breakout_warning(row["trade_setup"], True)
    api._apply_early_mover_signal_state(row, trigger)
    assert row["alertable_crypto"] is False
    assert row["trade_signal"] != "JETZT_TRADEN"
    assert CODE not in row.get("warning_codes", [])
    assert CODE not in row["trade_setup"].get("warning_codes", [])


def test_actual_later_crypto_retest_removes_stale_warning_not_other_risks():
    row = dict(_crypto_row("WAIT_FOR_RETEST"), warning_codes=["other"])
    apply_breakout_warning(row, True)
    api._apply_early_mover_signal_state(row, {"ok": True, "timeframe": "5m", "matched": ["retest_hold"]})
    assert row["retest_confirmed"] is True
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["warning_codes"] == ["other"]
    assert api._breakout_retest_warning_html(row) == ""


@pytest.mark.parametrize("action", ["NO_LONG_CHASE", "WAIT_FOR_BTC_CONFIRMATION", "WATCH_ONLY"])
def test_optional_retest_does_not_clear_other_crypto_action_blocks(action):
    row = {"trade_action": action}
    api._apply_early_mover_signal_state(row, {"ok": True, "timeframe": "5m", "matched": ["breakout"]})
    assert not row.get("alertable_crypto")
    assert CODE not in row.get("warning_codes", [])


@pytest.mark.parametrize("open_session", [True, False])
def test_cup_handle_is_not_misrepresented_as_postbreak_retest(monkeypatch, open_session):
    _mock_mail_env(monkeypatch, allowed=open_session)
    result = api._apply_cup_handle_strategy_filter(_mk_candidate(), {"min_dollar_volume": 2_000_000})
    assert result is not None
    if open_session:
        assert not result["daily_close_confirmed"]
        assert CODE not in result.get("warning_codes", [])
    else:
        assert result["daily_close_confirmed"]
        assert result["warning_codes"] == [CODE]
        assert result["trade_setup"]["warning_codes"] == [CODE]


@pytest.mark.parametrize("trigger_type,warning", [("fresh_5m_cross", True), ("opening_5m_hold", True), ("5m_retest_held", False)])
def test_cup_next_session_warning_reconstructed_from_proof(monkeypatch, trigger_type, warning):
    _mock_mail_env(monkeypatch, allowed=False)
    row = api._apply_cup_handle_strategy_filter(_mk_candidate(), {"min_dollar_volume": 2_000_000})
    stored = api._cup_handle_watch_row(row)
    result = api._promote_cup_handle_watch_row(stored, {
        "confirmed": True, "trigger_type": trigger_type, "trigger_observed_ts": NOW.timestamp(),
    })
    assert (CODE in result.get("warning_codes", [])) is warning
    assert (CODE in result["trade_setup"].get("warning_codes", [])) is warning


def test_full_intraday_momentum_scan_emits_warning_with_actual_proof(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1 and written[0][0] == rows
    assert rows[0]["Breakout_Freshness_Status"] == "FRESH_CROSS"
    assert rows[0]["retest_status"] == "not_confirmed"


def test_full_daily_swing_scan_emits_warning_without_realtime_request(monkeypatch):
    _wrapper_fixture(monkeypatch)
    session = swing.completed_sessions(NOW, 1)[0]
    data = payload(session, 102)
    data["results"][0].update(o=98, h=102, l=96)
    feed = swing.universe(swing.parse_grouped(data, session), {"TEST": {"c": 98, "v": 1_000_000}}, session)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", lambda *a: feed)
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **k: pytest.fail("no live dependency"))
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1
    assert rows[0]["Breakout_Freshness_Status"] == "DAILY_CONFIRMED"
    assert rows[0]["warning_codes"] == [CODE]
    assert swing.validate(rows[0], NOW)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("later_touch,expected", [(False, False), (True, True)])
def test_orb_launch_wick_is_not_retest_but_later_touch_is(direction, later_touch, expected):
    bars = [{"t": 1_000_000, "c": 11, "h": 11.2, "l": 9.5, "v": 200},
            {"t": 1_300_000, "c": 11.1, "h": 11.3, "l": 10 if later_touch else 10.5, "v": 150}]
    if direction == "SHORT":
        bars = [dict(b, c=20-b["c"], h=20-b["l"], l=20-b["h"]) for b in bars]
    result = api._orb_active_excursion_volume([{"v": 100}, {"v": 100}], bars, direction, 10, 10, 2)
    assert result["confirmed"] is True
    assert result["retest_confirmed"] is expected


@pytest.mark.parametrize("missing", ["breakout_confirmation", "retest_status", "warning_codes", "retest_warning"])
def test_renderer_requires_complete_producer_metadata(missing):
    row = breakout_warning_fields(True)
    row.pop(missing)
    assert not api._breakout_retest_warning_html(row)


def test_all_custom_mail_templates_have_escaped_deduplicated_warning_fallback():
    row = dict(ticker="<script>BAD</script>", **breakout_warning_fields(True))
    before = deepcopy(row)
    rendered = api._append_breakout_warning_summary("<html><body>Plan</body></html>", [row, row])
    assert row == before
    assert "<script>" not in rendered.lower() and "&lt;script&gt;" in rendered.lower()
    assert rendered.count("Ruecktest noch nicht bestaetigt") == 1
    assert rendered.endswith("</body></html>")
    assert api._append_breakout_warning_summary(rendered, [row]) == rendered
    assert api._append_breakout_warning_summary("Plan", [{"ticker": "NOT_A_BREAKOUT"}]) == "Plan"


def test_mixed_mail_rows_do_not_hide_missing_warning_of_a_second_symbol():
    first = dict(ticker="FIRST", **breakout_warning_fields(True))
    second = dict(ticker="SECOND", **breakout_warning_fields(True))
    body = "<body>" + api._breakout_retest_warning_html(first) + "</body>"
    rendered = api._append_breakout_warning_summary(body, [first, second])
    assert rendered.count('data-breakout-warning-for="FIRST"') == 1
    assert rendered.count('data-breakout-warning-for="SECOND"') == 1


def test_crypto_explosion_warning_survives_normalizer_and_expires(monkeypatch):
    from test_crypto_explosion_scanner import _candidate, _bars, _btc_context
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    # Inject only structural targets; actual trigger, freshness and risk checks run.
    monkeypatch.setattr(api, "apply_vrvp_to_trade_setup", lambda setup, *a, **k: dict(
        setup, tp1=13., tp2=14., tp1_is_projection=False, target_quality="STRUCTURAL",
        tp1_source="test confirmed resistance", barrier_gate=None, structure_status="ACCEPT"))
    bars5 = _bars(90, start=9.50, step=.004, volume=1000, last={
        "open": 10., "high": 10.14, "low": 9.98, "close": 10.12, "volume": 3200})
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900)
    bars4h = _bars(60, start=9.4, step=.006, volume=5000, interval=14400)
    row = api._score_crypto_explosion_candidate(_candidate(price=10.12, change=8.), bars5, bars15, bars4h)
    assert row["execution_trigger_ok"] is True and row["trade_signal"] == "JETZT_TRADEN"
    assert row["warning_codes"] == [CODE]
    normalized = api._normalize_crypto_long_signal(row)
    assert normalized["warning_codes"] == [CODE]
    assert normalized["trade_setup"]["warning_codes"] == [CODE]
    api._downgrade_expired_crypto_triggers([normalized], 9999)
    assert normalized["trade_signal"] == "WARTEN"
    assert CODE not in normalized.get("warning_codes", [])
    assert CODE not in normalized["trade_setup"].get("warning_codes", [])
