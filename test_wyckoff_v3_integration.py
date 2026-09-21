"""Cross-boundary V3 regression cases, private runtimes only."""
from copy import deepcopy
from datetime import timedelta

import pytest
import api
from modules import patterns, signal_tracker
from modules.wyckoff import MODEL
from test_wyckoff_engine import BASE, textbook_bars
from test_wyckoff_api_integration import candidate
from test_wyckoff_v3_contract import proof


def ready_row(direction="LONG"):
    strategy, raw = candidate(direction)
    row = api._apply_pattern_strategy_filter(raw, api.STRATEGIES[strategy])
    assert row
    row.update(Strategy=strategy, strategy=strategy, ticker="OFFLINE", Ticker="OFFLINE")
    return row


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_causal_trigger_is_carried_into_tracker_and_mail_identity(direction):
    row = ready_row(direction)
    evidence = row["wyckoff"]
    assert api._stock_wyckoff_row_contract_valid(row, as_of=BASE+timedelta(days=100))
    assert row["setup_key"] == "wyckoff:" + evidence["entry_trigger"]["trigger_id"]
    captured = signal_tracker.extract_execution_context(row)["confirmation"]["wyckoff"]
    assert captured["trigger_id"] == evidence["entry_trigger"]["trigger_id"]
    assert captured["structure_id"] == evidence["structure_id"]
    assert captured["event_ids"] == evidence["entry_trigger"]["event_ids"]
    assert captured["model"] == MODEL
    key = api._alert_signal_identity_key("stock_strategy", row)
    same_trigger = deepcopy(row)
    for name in ("entry", "Entry", "price", "Preis"):
        same_trigger[name] = float(row["wyckoff"]["trade"]["entry"]) + (.01 if direction == "LONG" else -.01)
    assert api._alert_signal_identity_key("stock_strategy", same_trigger) == key
    new_trigger = deepcopy(row)
    new_trigger["wyckoff"]["entry_trigger"]["trigger_id"] += "new"
    assert api._alert_signal_identity_key("stock_strategy", new_trigger) != key


def test_confirmation_snapshot_does_not_copy_arbitrary_private_fields():
    row = {"wyckoff": proof()}
    row["wyckoff"]["private_key"] = "DO-NOT-COPY"
    row["wyckoff"]["entry_trigger"]["event_ids"]["api_key"] = "DO-NOT-COPY"
    captured = signal_tracker.extract_execution_context(row)["confirmation"]["wyckoff"]
    assert "DO-NOT-COPY" not in str(captured)
    assert set(captured["event_ids"]) == {"origin", "reaction", "test", "breakout", "retest"}


def test_target_passed_keeps_structure_coloring_and_context_not_entry():
    bars = textbook_bars()
    bars[-1].update(open=126., high=132., low=125., close=130., volume=1000.)
    shown = patterns.detect_chart_patterns(bars, lookback=50, wyckoff_context={
        "bars": bars, "as_of": BASE+timedelta(days=100), "timeframe": "1D"})
    rows = [r for r in shown if r.get("model") == MODEL and r["direction"] == "LONG"]
    assert rows
    row = rows[0]
    assert row["trade_ready"] is False and row["trade"] is None
    assert row["structure_state"] != "failed"
    assert "Ungueltige Struktur" not in row["description"]


@pytest.mark.parametrize("field", ["phase", "direction", "structure_state", "origin_kind", "structure_type"])
@pytest.mark.parametrize("value", [[], {}])
def test_malformed_cache_enum_is_rejected_without_crashing(field, value):
    row = ready_row()
    row["wyckoff"][field] = value
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=BASE+timedelta(days=100))


def test_unrelated_strategy_with_partial_wyckoff_metadata_keeps_generic_identity():
    row = {"ticker": "OFFLINE", "Strategy": "Gap Momentum Long", "price": 100., "wyckoff": {}}
    assert api._alert_signal_identity_key("stock_strategy", row)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_selected_trigger_stop_cannot_be_widened_by_cached_plan(direction):
    row = ready_row(direction)
    row["wyckoff"]["trade"]["stop"] += -1. if direction == "LONG" else 1.
    assert not api._stock_wyckoff_row_contract_valid(row, as_of=BASE+timedelta(days=100))


def test_bad_high_scoring_engine_payload_does_not_hide_valid_trigger(monkeypatch):
    from modules import wyckoff
    strategy, candidate_row = candidate("LONG")
    report = wyckoff.analyze_wyckoff(candidate_row["_daily_bars"],
                                   as_of=candidate_row["_pattern_as_of"], timeframe="1D", direction="LONG")
    report["patterns"].insert(0, {"direction": "LONG", "trade_ready": True, "score": 1000})
    report["patterns"].insert(0, None)
    monkeypatch.setattr(wyckoff, "analyze_wyckoff", lambda *a, **k: report)
    row = api._apply_pattern_strategy_filter(candidate_row, api.STRATEGIES[strategy])
    assert row and row["pattern_score"] != 1000
