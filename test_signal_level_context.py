#!/usr/bin/env python3
"""Focused tests for additive, versioned signal execution-context telemetry."""

import copy
import json
import sqlite3

import pytest

import modules.signal_tracker as st


def _base_row(**overrides):
    row = {
        "Ticker": "CTX",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 105.0,
        "TP2": 110.0,
    }
    row.update(overrides)
    return row


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "context.sqlite"))
    monkeypatch.setattr(
        st,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(tmp_path / "context_delivery.sqlite"),
    )
    return st


def _stored_signal():
    with sqlite3.connect(st.SIGNAL_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT 1").fetchone()
    assert row is not None
    return dict(row)


def test_missing_execution_evidence_is_explicitly_unavailable():
    context = st.extract_execution_context(_base_row())

    assert context["schema_version"] == st.EXECUTION_CONTEXT_SCHEMA_VERSION == 2
    for section in (
        "levels",
        "target_reachability",
        "nearest_barrier",
        "confirmation",
        "event",
        "liquidity",
        "grouping",
        "experiment",
    ):
        assert context[section] == {"availability": "unavailable"}


def test_execution_context_extracts_only_supplied_evidence_deterministically():
    row = _base_row(
        level_model="final_top_level_model",
        target_reachability={
            "data_available": True,
            "horizon": "swing",
            "provenance": "native",
            "configured_budget_atr": 4.0,
            "budget_configured": True,
            "stop_distance_atr": 1.25,
            "tp1_distance_atr": 1.25,
            "tp2_distance_atr": 2.5,
            "within_budget": True,
            "issues": [],
            "untrusted_extra": "must not persist",
        },
        trade_setup={
            "level_model": "structure_first_v2+vrvp",
            "stop_source": "4h demand-zone invalidation",
            "tp1_source": "daily resistance",
            "tp2_source": "weekly resistance",
            "target_quality": "STRUCTURAL",
            "nearest_barrier": {
                "price": 103.0,
                "side": "resistance",
                "source": "weekly_supply_zone",
                "distance_r": 0.6,
                "distance_atr": 0.75,
                "timeframe": "1w",
                "zone_id": "zone-weekly-17",
                "reclaimed": False,
            },
        },
        execution_trigger_ok=False,
        breakout_confirmed=True,
        retest_confirmed=True,
        reclaim_confirmed=False,
        entry_confirmation_type="RETEST_CONFIRMED",
        breakout_freshness_status="fresh",
        intraday_trigger={
            "matched": True,
            "reason": "5m close held above breakout",
            "timeframe": "5m",
            "checked_at": "2026-08-23T08:00:00+00:00",
            "private_blob": "must not persist",
        },
        event_status="verified_upcoming",
        event_type="earnings",
        event_date="2026-08-27",
        days_to_event=4,
        event_data_status="fresh",
        final_quote_bid=99.9,
        final_quote_ask=100.1,
        final_quote_spread_bps=20.0,
        final_quote_depth_10bps_min_usd=50_000.0,
        final_quote_depth_25bps_min_usd=125_000.0,
        final_quote_depth_50bps_min_usd=250_000.0,
        funding_rate_fraction=0.0,
        funding_rate_raw=0.0,
        funding_rate_unit="fraction",
        funding_interval_hours=8.0,
        OI_ChangePct=12.5,
        OI_HistoryAgeSeconds=3600,
        PerpOI=4_500_000.0,
        sector="Technology",
        industry="Semiconductors",
        group_key="SEMIS",
        group_verified=True,
        experiment_context={
            "experiment_id": "level-model-shadow-2026-08",
            "variant_id": "structure-v2",
        },
    )

    first = st.extract_execution_context(row)
    second = st.extract_execution_context(copy.deepcopy(row))

    assert first == second
    assert st._execution_context_json(row) == st._execution_context_json(copy.deepcopy(row))
    assert first["levels"] == {
        "availability": "available",
        "level_model": "final_top_level_model",
        "stop_source": "4h demand-zone invalidation",
        "tp1_source": "daily resistance",
        "tp2_source": "weekly resistance",
        "target_quality": "STRUCTURAL",
    }
    assert first["target_reachability"]["data_available"] is True
    assert first["target_reachability"]["level_provenance"] == "native"
    assert first["target_reachability"]["tp2_distance_atr"] == pytest.approx(2.5)
    assert "untrusted_extra" not in first["target_reachability"]
    assert first["nearest_barrier"] == {
        "availability": "available",
        "kind": "nearest_barrier",
        "price": 103.0,
        "distance_r": 0.6,
        "distance_atr": 0.75,
        "side": "resistance",
        "source": "weekly_supply_zone",
        "timeframe": "1w",
        "zone_id": "zone-weekly-17",
        "reclaimed": False,
    }
    assert first["confirmation"]["execution_trigger_ok"] is False
    assert first["confirmation"]["reclaim_confirmed"] is False
    assert first["confirmation"]["intraday_trigger"]["matched"] is True
    assert "private_blob" not in first["confirmation"]["intraday_trigger"]
    assert first["event"]["days_to_event"] == pytest.approx(4.0)
    assert first["event"]["days_source"] == "days_to_event"
    assert first["liquidity"]["funding_rate_pct"] == pytest.approx(0.0)
    assert first["liquidity"]["funding_rate_raw"] == pytest.approx(0.0)
    assert first["liquidity"]["funding_rate_unit"] == "fraction"
    assert first["liquidity"]["funding_interval_hours"] == pytest.approx(8.0)
    assert first["liquidity"]["spread_to_risk_r"] == pytest.approx(0.04)
    assert first["liquidity"]["spread_to_risk_pct"] == pytest.approx(4.0)
    assert (
        first["liquidity"]["spread_to_risk_source"]
        == "derived_bid_ask_over_planned_risk"
    )
    assert first["grouping"]["group_verified"] is True
    assert first["experiment"]["experiment_id"] == "level-model-shadow-2026-08"
    assert first["experiment"]["variant_id"] == "structure-v2"


def test_final_row_barrier_wins_over_stale_nested_setup_telemetry():
    row = _base_row(
        target_quality="WEAK_STRUCTURAL_TARGETS",
        nearest_barrier={
            "zone_id": "z_new",
            "price": 110.0,
            "side": "resistance",
            "action": None,
            "reclaimed": True,
        },
        trade_setup={
            "target_quality": "STRUCTURAL_VRVP",
            "nearest_barrier": {
                "zone_id": "z_old",
                "price": 105.0,
                "side": "resistance",
                "action": "BREAK_RECLAIM_REQUIRED",
                "reclaimed": False,
            }
        },
    )

    barrier = st.extract_execution_context(row)["nearest_barrier"]

    assert barrier["zone_id"] == "z_new"
    assert barrier["price"] == 110.0
    assert barrier["reclaimed"] is True
    assert "action" not in barrier
    assert st.extract_execution_context(row)["levels"]["target_quality"] == "WEAK_STRUCTURAL_TARGETS"


def test_funding_fraction_and_percent_normalize_to_same_canonical_percentage():
    fraction = st.extract_execution_context(_base_row(
        funding_rate_fraction=0.001,
        funding_rate_raw=0.001,
        funding_rate_unit="fraction",
        funding_interval_hours=8,
    ))["liquidity"]
    percent = st.extract_execution_context(_base_row(
        funding_rate_pct=0.1,
        funding_rate_raw=0.1,
        funding_rate_unit="percent",
        funding_interval_hours=8,
    ))["liquidity"]

    assert fraction["funding_rate_pct"] == pytest.approx(0.1)
    assert percent["funding_rate_pct"] == pytest.approx(0.1)
    assert fraction["funding_rate_raw"] == pytest.approx(0.001)
    assert percent["funding_rate_raw"] == pytest.approx(0.1)
    assert fraction["funding_rate_unit"] == "fraction"
    assert percent["funding_rate_unit"] == "percent"


def test_legacy_ambiguous_funding_is_raw_only_not_calibration_value():
    liquidity = st.extract_execution_context(_base_row(FundingRate=0.001))["liquidity"]

    assert liquidity["funding_rate_raw"] == pytest.approx(0.001)
    assert liquidity["funding_rate_unit"] == "unknown_legacy"
    assert "funding_rate_pct" not in liquidity


def test_explicit_final_nulls_clear_stale_nested_level_and_barrier_context():
    row = _base_row(
        target_quality=None,
        nearest_barrier=None,
        trade_setup={
            "target_quality": "STRUCTURAL_VRVP",
            "nearest_barrier": {
                "zone_id": "stale-zone",
                "price": 105.0,
                "side": "resistance",
                "action": "BREAK_RECLAIM_REQUIRED",
            },
        },
    )

    context = st.extract_execution_context(row)

    assert context["levels"] == {"availability": "unavailable"}
    assert context["nearest_barrier"] == {"availability": "unavailable"}


def test_alertable_crypto_is_not_persisted_as_execution_trigger_proof():
    confirmation = st.extract_execution_context(
        _base_row(alertable_crypto=True)
    )["confirmation"]

    assert confirmation["alertable_crypto"] is True
    assert "execution_trigger_ok" not in confirmation


def test_record_persists_context_for_trade_and_shadow_without_mutating_identity(tracker):
    trade_row = _base_row(
        Ticker="TRADECTX",
        level_model="structure_first_v2",
        experiment_id="barrier-shadow-v1",
        variant_id="control",
    )
    original = copy.deepcopy(trade_row)
    fields = tracker._prepare_identity_fields(
        tracker.extract_signal_fields(trade_row), "stock_strategy", "stock"
    )
    row_token_before = tracker._canonical_delivery_row_token(
        "stock_strategy", fields, "stock"
    )
    intent_before = tracker.build_alert_delivery_intent_key(
        "stock_strategy", [trade_row]
    )

    assert tracker.record_alert_signals("stock_strategy", [trade_row]) == 1
    stored = _stored_signal()
    persisted = json.loads(stored["execution_context_json"])

    assert persisted == tracker.extract_execution_context(original)
    assert trade_row == original
    assert tracker.build_alert_delivery_intent_key("stock_strategy", [trade_row]) == intent_before
    fields_after = tracker._prepare_identity_fields(
        tracker.extract_signal_fields(trade_row), "stock_strategy", "stock"
    )
    assert (
        tracker._canonical_delivery_row_token("stock_strategy", fields_after, "stock")
        == row_token_before
    )

    shadow_row = _base_row(
        Ticker="SHADOWCTX",
        block_reasons="swing_day_move_extended_wait_retest",
        experiment_id="barrier-shadow-v1",
        variant_id="wait-retest",
    )
    assert tracker.record_alert_signals(
        "stock_strategy", [shadow_row], mail_class="shadow"
    ) == 1
    shadow = _stored_signal()
    assert shadow["mail_class"] == "shadow"
    shadow_context = json.loads(shadow["execution_context_json"])
    assert shadow_context["experiment"]["experiment_id"] == "barrier-shadow-v1"
    assert shadow_context["experiment"]["variant_id"] == "wait-retest"


def test_schema_migration_keeps_old_rows_and_adds_context_to_new_rows(
    tmp_path, monkeypatch
):
    db_path = tmp_path / "legacy_context.sqlite"
    legacy_schema = st._SCHEMA.replace("    ,execution_context_json TEXT\n", "")
    with sqlite3.connect(db_path) as conn:
        conn.execute(legacy_schema)
        conn.execute(
            "INSERT INTO signals (created_at, scanner, ticker, entry, stop, tp1, tp2) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("2026-08-01T00:00:00+00:00", "breakout", "LEGACY", 100, 95, 105, 110),
        )
        conn.commit()

    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(db_path))
    monkeypatch.setattr(
        st,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(tmp_path / "legacy_context_delivery.sqlite"),
    )
    assert st.record_alert_signals(
        "breakout", [_base_row(Ticker="NEWCTX", level_model="structure_first_v2")]
    ) == 1

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        rows = {
            row["ticker"]: dict(row)
            for row in conn.execute("SELECT * FROM signals ORDER BY id").fetchall()
        }

    assert "execution_context_json" in columns
    assert rows["LEGACY"]["execution_context_json"] is None
    assert json.loads(rows["NEWCTX"]["execution_context_json"])["schema_version"] == 2
