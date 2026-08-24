#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pytest-Suite Shadow-Tracking (AUDIT 2026-07-31).

Signale, die NUR an den Swing-Timing-Gates (Chase-Schutz) scheitern, werden
still als mail_class='shadow' in den Signal-Tracker geloggt und mit denselben
Regeln evaluiert — aber:
  - sie loesen KEINE Mails aus (weder Entry- noch Exit-/BE-Update-Mails),
  - sie fliessen NIE in Win-Rate/Verdikt (load_performance_summary),
  - sie kollidieren nicht mit dem Dedupe echter Trade-Signale,
  - sie sind separat auswertbar (shadow_summary / Wochenreport-Sektion).

Komplett offline: tmp-SQLite via SIGNAL_DB_PATH-Monkeypatch, Fake-Fetcher,
gestubbte Pipeline-/Mail-Funktionen (Muster test_signal_tracker.py /
test_exit_update_mails.py / test_email_alert_audit.py).
"""
import ast
import inspect
import json
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import api
import bg_service
import modules.signal_tracker as st


# ── Helpers ──────────────────────────────────────────────────────────────────
def _base_row(**overrides):
    """Plausible LONG-Alert-Row: Entry 100, Stop 95 (Risk 5), TP1 105, TP2 110."""
    row = {
        "Ticker": "AAPL", "Entry": 100.0, "StopLoss": 95.0,
        "TP1": 105.0, "TP2": 110.0, "Preis": 100.0,
    }
    row.update(overrides)
    return row


def _db_rows(query="SELECT * FROM signals ORDER BY id", params=()):
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    """Frische tmp-DB pro Test (SIGNAL_DB_PATH wird pro Aufruf gelesen)."""
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "shadow_test.sqlite"))
    return st


# ── 1. Whitelist-Helper _shadow_trackable_reasons ────────────────────────────
def _state(reasons):
    return {"alertable_now": False, "suppression_reasons": list(reasons)}


def test_shadow_trackable_all_timing_reasons():
    reasons = api._shadow_trackable_reasons(
        "stock_strategy",
        _state(["swing_day_move_extended_wait_retest", "swing_top_entry_extended_wait_retest"]),
        "stocks",
        False,
    )
    assert reasons == ["swing_day_move_extended_wait_retest", "swing_top_entry_extended_wait_retest"]


def test_shadow_trackable_rejects_cooldown_mix():
    # Cooldown = das Signal wurde bereits gemailt/geloggt — keine Messung.
    assert api._shadow_trackable_reasons(
        "stock_strategy",
        _state(["swing_day_move_extended_wait_retest", "cooldown_active"]),
        "stocks",
        False,
    ) is None


def test_shadow_trackable_rejects_base_blockers():
    for reason in ("score_below_alert_threshold", "grade_below_alert_threshold",
                   "rvol_below_alert_threshold", "non_common_stock_product",
                   "missing_ticker"):
        assert api._shadow_trackable_reasons(
            "stock_strategy", _state([reason]), "stocks", False,
        ) is None, reason


def test_shadow_trackable_rejects_plan_and_data_gates():
    for reason in ("invalid_trade_plan", "estimated_trade_plan",
                   "trade_rr_below_threshold", "intraday_unconfirmed_pattern",
                   "swing_short_not_down_enough", "missing_current_drop",
                   "rvol_below_bear_threshold", "dailyclose_dedupe_active"):
        assert api._shadow_trackable_reasons(
            "stock_strategy", _state([reason]), "stocks", False,
        ) is None, reason


def test_shadow_trackable_scope_guards():
    state = _state(["swing_day_move_extended_wait_retest"])
    assert api._shadow_trackable_reasons("stock_strategy", state, "crypto", False) is None
    assert api._shadow_trackable_reasons("stock_strategy", state, "stocks", True) is None
    assert api._shadow_trackable_reasons("early_movers", state, "stocks", False) is None
    assert api._shadow_trackable_reasons("crypto_strategy", state, "crypto", False) is None
    assert api._shadow_trackable_reasons("stock_strategy", _state([]), "stocks", False) is None


def test_shadow_whitelist_matches_swing_rule_sources():
    """Fail-closed-Registry: jeder swing_*-Grund aus den beiden Regel-Funktionen
    ist entweder in der Whitelist ODER bewusst ausgenommen. Ein neuer Gate-
    Grund zwingt zu einer expliziten Entscheidung hier im Test."""
    excluded = {"swing_short_not_down_enough"}  # "kein Setup", kein Timing-Gate
    emitted = set()
    for func in (api._stock_swing_rule_reasons, api._stock_swing_short_rule_reasons):
        source = inspect.getsource(func)
        emitted.update(re.findall(r'reasons\.append\("([a-z0-9_]+)"\)', source))
    swing_emitted = {reason for reason in emitted if reason.startswith("swing")}
    assert swing_emitted, "Registry leer — Quell-Muster verändert?"
    unaccounted = swing_emitted - set(api._SHADOW_TRACKABLE_TIMING_REASONS) - excluded
    assert not unaccounted, f"Neue Swing-Gruende ohne Shadow-Entscheidung: {unaccounted}"


# ── 2. Tracker: record / dedupe / summary / shadow_summary ───────────────────
def test_record_shadow_logs_with_block_reasons(tracker):
    row = _base_row(block_reasons="swing_day_move_extended_wait_retest")
    assert tracker.record_alert_signals("stock_strategy", [row], mail_class="shadow") == 1
    sig = _db_rows("SELECT * FROM signals WHERE ticker = 'AAPL'")[0]
    assert sig["mail_class"] == "shadow"
    assert sig["block_reasons"] == "swing_day_move_extended_wait_retest"
    assert sig["status"] == "OPEN"


def test_record_still_rejects_other_mail_classes(tracker):
    assert tracker.record_alert_signals("stock_strategy", [_base_row()], mail_class="watch") == 0
    assert tracker.record_alert_signals("stock_strategy", [_base_row()], mail_class="info") == 0
    assert tracker.get_signal_count() == 0


def test_shadow_trade_dedupe_isolation(tracker):
    # Trade OPEN desselben Tickers blockiert das Shadow-Signal NICHT ...
    assert tracker.record_alert_signals("stock_strategy", [_base_row()], mail_class="trade") == 1
    assert tracker.record_alert_signals(
        "stock_strategy", [_base_row(block_reasons="swing_extended_wait_retest")],
        mail_class="shadow") == 1
    # ... aber ein zweites Shadow-Signal desselben Tickers (noch OPEN) schon.
    assert tracker.record_alert_signals(
        "stock_strategy", [_base_row(block_reasons="swing_extended_wait_retest")],
        mail_class="shadow") == 0
    # ... und das Shadow-Signal blockiert keine zweite Trade-Log-Zeile-Regel:
    # ein weiteres TRADE-Signal desselben Tickers wird vom Trade-Dedupe
    # geblockt (unverändertes Alt-Verhalten), nicht vom Shadow-Eintrag.
    assert tracker.record_alert_signals("stock_strategy", [_base_row()], mail_class="trade") == 0
    rows = _db_rows()
    assert len(rows) == 2
    assert {r["mail_class"] for r in rows} == {"trade", "shadow"}


def test_summary_excludes_shadow_rows(tracker):
    tracker.record_alert_signals("stock_strategy", [_base_row()], mail_class="trade")
    tracker.record_alert_signals(
        "stock_strategy", [_base_row(Ticker="BHC", block_reasons="swing_multi_day_exhausted_no_chase")],
        mail_class="shadow")
    summary = tracker.load_performance_summary(days=90)
    assert summary["total"]["signals"] == 1
    assert summary["total"]["open"] == 1
    assert "stock_strategy" in summary["per_scanner"]
    assert summary["per_scanner"]["stock_strategy"]["signals"] == 1
    # recent enthaelt nur das Trade-Signal
    assert [r["ticker"] for r in summary["recent"]] == ["AAPL"]


def test_shadow_summary_aggregates(tracker):
    tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="SH1", block_reasons="swing_day_move_extended_wait_retest,swing_top_entry_extended_wait_retest")],
        mail_class="shadow")
    tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="SH2", block_reasons="swing_day_move_extended_wait_retest")],
        mail_class="shadow")
    # Trade-Signal darf die Shadow-Statistik nicht verfaelschen.
    tracker.record_alert_signals("stock_strategy", [_base_row(Ticker="TR1")], mail_class="trade")
    # Ein Shadow-Signal entscheiden: SH1 per Fake-Fetcher auf TP2 laufen lassen.
    d0 = _db_rows("SELECT created_at FROM signals WHERE ticker = 'SH1'")[0]["created_at"][:10]
    from datetime import date, timedelta
    day = date.fromisoformat(d0)
    sessions = []
    cursor = day
    while len(sessions) < 2:
        cursor += timedelta(days=1)
        if st._is_us_equity_session(cursor):
            sessions.append(cursor)

    def fetcher(ticker, since_iso_date):
        if ticker != "SH1":
            return []
        return [
            {"date": sessions[0].isoformat(),
             "open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0,
             "interval_complete": True},
            {"date": sessions[1].isoformat(),
             "open": 106.0, "high": 111.0, "low": 104.0, "close": 110.5,
             "interval_complete": True},
        ]

    result = tracker.evaluate_open_signals(stock_daily_fetcher=fetcher)
    assert result["evaluated"] == 3  # beide Shadow + das Trade-Signal
    # Der Fake-Fetcher liefert bewusst Folge-Sessions nach der Test-Uhr. Setze
    # deshalb auch den terminalen Evidenzzeitpunkt kausal hinter den Fill.
    summary_as_of = f"{sessions[-1].isoformat()}T23:59:59+00:00"
    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET closed_at=? WHERE ticker='SH1'",
            (summary_as_of,),
        )
    summary = tracker.shadow_summary(days=90, as_of=summary_as_of)
    total = summary["total"]
    assert total["signals"] == 2
    assert total["open"] == 1
    assert total["decided_signals"] == 1
    assert total["wins"] == 1
    assert total["win_rate_pct"] == 100.0
    assert total["avg_r"] == pytest.approx(2.0)  # TP2: (110-100)/5
    assert summary["per_reason"]["swing_day_move_extended_wait_retest"] == 2
    assert summary["per_reason"]["swing_top_entry_extended_wait_retest"] == 1
    assert summary["per_scanner"] == {"stock_strategy": 2}
    assert summary["recent"][0]["block_reasons"]


def test_shadow_summary_empty_is_neutral(tracker):
    summary = tracker.shadow_summary(days=7)
    assert summary["total"]["signals"] == 0
    assert summary["total"]["avg_r"] is None
    assert summary["per_reason"] == {}
    assert summary["recent"] == []


def test_shadow_summary_adds_context_breakdowns_without_changing_legacy_keys(tracker):
    rich = _base_row(
        Ticker="RICH",
        strategy="breakout_retest",
        trade_horizon="swing",
        market_regime="RISK_ON",
        block_reasons="swing_4h_rejection_wait_reclaim,market_regime_yellow",
        level_model="confluence_v2",
        target_quality="strong",
        stop_source="support_zone",
        tp1_source="resistance_zone",
        tp2_source="fibonacci_extension",
        nearest_barrier={
            "side": "resistance",
            "source": "vrvp_poc",
            "timeframe": "4h",
            "distance_r": 1.25,
            "action": "wait_reclaim",
        },
        experiment_context={
            "experiment_id": "level-shadow-2026-08",
            "variant_id": "wait-retest",
        },
    )
    assert tracker.record_alert_signals(
        "stock_strategy", [rich], mail_class="shadow"
    ) == 1
    assert tracker.record_alert_signals(
        "strategy_scan",
        [_base_row(Ticker="LEGACY", block_reasons="")],
        mail_class="shadow",
    ) == 1

    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "code_revision='abc123def456', evaluation_horizon_bars=1, "
            "created_at='2026-08-10T12:00:00+00:00', "
            "entry_filled_at='2026-08-10T12:05:00+00:00', "
            "entry_fill_price=100.0, closed_at='2026-08-14T12:00:00+00:00' "
            "WHERE ticker='RICH'"
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "strategy=NULL, trade_horizon=NULL, market_regime=NULL, "
            "code_revision=NULL, execution_context_json='not-json', "
            "evaluation_horizon_bars=1, "
            "created_at='2026-08-11T12:00:00+00:00', "
            "entry_filled_at=NULL, entry_fill_price=NULL, "
            "closed_at='2026-08-15T12:00:00+00:00' "
            "WHERE ticker='LEGACY'"
        )

    summary = tracker.shadow_summary(
        days=90,
        as_of=datetime(2026, 8, 24, 12, tzinfo=timezone.utc),
    )

    # Bestehender Vertrag bleibt erhalten.
    assert summary["total"]["signals"] == 2
    assert summary["total"]["decided_signals"] == 1
    assert summary["total"]["wins"] == 1
    assert summary["total"]["win_rate_pct"] == 100.0
    assert summary["total"]["avg_r"] == 2.0
    assert summary["total"]["sum_r"] == 2.0
    assert summary["per_scanner"] == {
        "stock_strategy": 1,
        "strategy_scan": 1,
    }
    assert summary["per_reason"]["swing_4h_rejection_wait_reclaim"] == 1

    breakdowns = summary["breakdowns"]
    assert set(summary["breakdown_dimensions"]) == {
        "block_reason", "scanner", "asset_class", "strategy", "direction",
        "horizon", "market_regime", "code_revision", "level_model",
        "target_quality", "stop_source", "tp1_source", "tp2_source",
        "barrier_side", "barrier_timeframe", "barrier_source",
        "barrier_action", "experiment_variant",
    }
    rich_bucket = breakdowns["experiment_variant"][
        "level-shadow-2026-08 / wait-retest"
    ]
    assert rich_bucket["signals"] == 1
    assert rich_bucket["decided"] == 1
    assert rich_bucket["wins"] == 1
    assert rich_bucket["avg_r"] == pytest.approx(2.0)
    assert rich_bucket["win_rate_wilson_95"] is not None
    assert rich_bucket["sample_reliable"] is False
    assert breakdowns["level_model"]["confluence_v2"]["sum_r"] == 2.0
    assert breakdowns["asset_class"]["stock"]["signals"] == 2
    assert breakdowns["target_quality"]["strong"]["signals"] == 1
    assert breakdowns["stop_source"]["support_zone"]["signals"] == 1
    assert breakdowns["tp1_source"]["resistance_zone"]["signals"] == 1
    assert breakdowns["tp2_source"]["fibonacci_extension"]["signals"] == 1
    assert breakdowns["barrier_side"]["resistance"]["signals"] == 1
    assert breakdowns["barrier_timeframe"]["4h"]["signals"] == 1
    assert breakdowns["barrier_source"]["vrvp_poc"]["signals"] == 1
    assert breakdowns["barrier_action"]["wait_reclaim"]["signals"] == 1

    # Fehlende Legacy-Spalten und defektes JSON werden sichtbar, nie positiv
    # aufgefuellt oder unter einem guenstigen Kontext einsortiert.
    assert breakdowns["block_reason"]["legacy_unknown"]["signals"] == 1
    assert breakdowns["strategy"]["legacy_unknown"]["signals"] == 1
    assert breakdowns["horizon"]["legacy_unknown"]["signals"] == 1
    assert breakdowns["market_regime"]["legacy_unknown"]["signals"] == 1
    assert breakdowns["code_revision"]["legacy_unknown"]["signals"] == 1
    assert breakdowns["level_model"]["unavailable"]["signals"] == 1
    assert breakdowns["target_quality"]["unavailable"]["signals"] == 1
    assert breakdowns["barrier_side"]["unavailable"]["signals"] == 1
    assert breakdowns["experiment_variant"]["unavailable"]["signals"] == 1
    assert breakdowns["strategy"]["legacy_unknown"]["unresolved"] == 1
    assert breakdowns["strategy"]["legacy_unknown"]["decided"] == 0
    assert breakdowns["strategy"]["legacy_unknown"]["avg_r"] is None
    assert summary["total"]["control_resolved_signals"] == 1
    assert summary["total"]["control_unresolved"] == 1
    assert summary["total"]["sample_reliable"] is False

    recent = {row["ticker"]: row for row in summary["recent"]}
    assert recent["RICH"]["strategy"] == "breakout_retest"
    assert recent["RICH"]["horizon"] == "swing"
    assert recent["RICH"]["market_regime"] == "RISK_ON"
    assert recent["RICH"]["code_revision"] == "abc123def456"
    assert recent["RICH"]["context"]["levels"]["level_model"] == "confluence_v2"
    assert recent["RICH"]["context"]["barrier"]["side"] == "resistance"
    assert recent["RICH"]["context"]["experiment"]["variant_id"] == "wait-retest"
    assert recent["RICH"]["control_resolution"] == "resolved"
    assert recent["RICH"]["r_realized"] == 2.0
    assert recent["LEGACY"]["context"]["levels"] == {
        "availability": "unavailable"
    }
    assert recent["LEGACY"]["control_resolution"] == "unresolved"
    assert recent["LEGACY"]["r_realized"] is None


def test_shadow_summary_mature_only_uses_fully_observed_cohort(tracker):
    assert tracker.record_alert_signals(
        "crypto_strategy",
        [
            _base_row(Ticker="MATURE", block_reasons="regime_cooldown"),
            _base_row(Ticker="YOUNG", block_reasons="regime_cooldown"),
        ],
        mail_class="shadow",
    ) == 2
    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET created_at='2026-08-10T12:00:00+00:00', "
            "entry_filled_at='2026-08-10T12:05:00+00:00', "
            "entry_fill_price=100.0, closed_at='2026-08-16T12:00:00+00:00', "
            "status='TP2_HIT', r_realized=2.0 WHERE ticker='MATURE'"
        )
        conn.execute(
            "UPDATE signals SET created_at='2026-08-23T12:00:00+00:00', "
            "entry_filled_at='2026-08-23T12:05:00+00:00', "
            "entry_fill_price=100.0, closed_at='2026-08-23T13:00:00+00:00', "
            "status='STOP_HIT', r_realized=-1.0 WHERE ticker='YOUNG'"
        )

    as_of = datetime(2026, 8, 24, 12, tzinfo=timezone.utc)
    diagnostic = tracker.shadow_summary(days=30, mature_only=False, as_of=as_of)
    mature = tracker.shadow_summary(days=30, mature_only=True, as_of=as_of)

    assert diagnostic["cohort_mode"] == "created_in_window"
    assert diagnostic["total"]["signals"] == 2
    assert mature["cohort_mode"] == "fully_observed"
    assert mature["cohort"] == {
        "mode": "fully_observed",
        "selection_basis": "matured_in_window",
        "mature_only": True,
        "created_in_window": 2,
        "matured_in_window": 1,
        "included_signals": 1,
        "excluded_not_mature": 1,
    }
    assert mature["total"]["signals"] == 1
    assert [row["ticker"] for row in mature["recent"]] == ["MATURE"]


def test_shadow_summary_historical_as_of_never_uses_later_closure(tracker):
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="FUTURE", block_reasons="swing_extended_wait_retest")],
        mail_class="shadow",
    ) == 1
    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET created_at='2026-08-10T12:00:00+00:00', "
            "entry_filled_at='2026-08-10T12:05:00+00:00', "
            "entry_fill_price=100.0, status='TP2_HIT', r_realized=2.0, "
            "closed_at='2026-08-25T12:00:00+00:00' WHERE ticker='FUTURE'"
        )

    summary = tracker.shadow_summary(
        days=30,
        mature_only=False,
        as_of=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
    )

    assert summary["total"]["decided_signals"] == 0
    assert summary["total"]["control_unresolved"] == 1
    assert summary["recent"][0]["control_resolution"] == "unresolved"
    assert summary["recent"][0]["r_realized"] is None


def test_shadow_summary_is_strict_json_safe_for_nonfinite_context_and_overflow(tracker):
    assert tracker.record_alert_signals(
        "stock_strategy",
        [
            _base_row(
                Ticker="HUGE1",
                block_reasons="swing_extended_wait_retest",
                nearest_barrier={
                    "side": "resistance",
                    "source": "vrvp_poc",
                    "timeframe": "4h",
                    "distance_atr": float("nan"),
                },
            ),
            _base_row(
                Ticker="HUGE2",
                block_reasons="swing_extended_wait_retest",
                nearest_barrier={
                    "side": "resistance",
                    "source": "vrvp_poc",
                    "timeframe": "4h",
                    "distance_atr": float("inf"),
                },
            ),
        ],
        mail_class="shadow",
    ) == 2
    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        for ticker in ("HUGE1", "HUGE2"):
            conn.execute(
                "UPDATE signals SET created_at='2026-08-10T12:00:00+00:00', "
                "entry_filled_at='2026-08-10T12:05:00+00:00', "
                "entry_fill_price=100.0, status='TP2_HIT', r_realized=1e308, "
                "closed_at='2026-08-15T12:00:00+00:00' WHERE ticker=?",
                (ticker,),
            )

    summary = tracker.shadow_summary(
        days=30,
        mature_only=False,
        as_of=datetime(2026, 8, 20, 12, tzinfo=timezone.utc),
    )

    assert summary["total"]["decided_signals"] == 2
    assert summary["total"]["avg_r"] is None
    assert summary["total"]["sum_r"] is None
    assert summary["total"]["sample_reliable"] is False
    assert all(
        "distance_atr" not in row["context"]["barrier"]
        for row in summary["recent"]
    )
    json.dumps(summary, allow_nan=False)


def test_shadow_experiment_breakdown_preserves_long_and_partial_identity(tracker):
    experiment_id = "experiment-" + ("x" * 149)
    first_variant = "variant-" + ("a" * 150)
    second_variant = "variant-" + ("a" * 149) + "b"
    rows = [
        _base_row(
            Ticker="EXP1",
            block_reasons="swing_extended_wait_retest",
            experiment_context={
                "experiment_id": experiment_id,
                "variant_id": first_variant,
            },
        ),
        _base_row(
            Ticker="EXP2",
            block_reasons="swing_extended_wait_retest",
            experiment_context={
                "experiment_id": experiment_id,
                "variant_id": second_variant,
            },
        ),
        _base_row(
            Ticker="EXP3",
            block_reasons="swing_extended_wait_retest",
            experiment_context={"experiment_id": "known-experiment"},
        ),
    ]
    assert tracker.record_alert_signals(
        "stock_strategy", rows, mail_class="shadow"
    ) == 3

    summary = tracker.shadow_summary(
        days=30,
        mature_only=False,
        as_of=datetime.now(timezone.utc),
    )
    keys = set(summary["breakdowns"]["experiment_variant"])

    assert f"{experiment_id} / {first_variant}" in keys
    assert f"{experiment_id} / {second_variant}" in keys
    assert "known-experiment / unavailable" in keys


def test_shadow_summary_import_is_additive_and_isolated():
    tree = ast.parse(inspect.getsource(api))
    imports = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "modules.signal_tracker"
    ]
    shadow_imports = [
        node for node in imports if any(alias.name == "shadow_summary" for alias in node.names)
    ]

    assert len(shadow_imports) == 1
    assert [alias.name for alias in shadow_imports[0].names] == ["shadow_summary"]


def test_shadow_performance_api_clamps_days_and_defaults_to_mature(monkeypatch):
    captured = []
    monkeypatch.setattr(
        api,
        "_require_admin",
        lambda authorization: ({"email": "admin@example.com"}, "admin@example.com"),
    )
    monkeypatch.setattr(
        api,
        "shadow_summary",
        lambda days=90, mature_only=False: captured.append((days, mature_only))
        or {"window_days": days, "cohort_mode": "fully_observed"},
    )

    api.api_shadow_signal_performance(days=0, authorization="Bearer admin")
    api.api_shadow_signal_performance(days=9999, authorization="Bearer admin")
    api.api_shadow_signal_performance(
        days=7, mature_only=False, authorization="Bearer admin"
    )
    api.api_shadow_signal_performance(authorization="Bearer admin")

    assert captured == [(1, True), (365, True), (7, False), (30, True)]
    signature = inspect.signature(api.api_shadow_signal_performance)
    assert signature.parameters["days"].default.default == 30
    assert signature.parameters["mature_only"].default.default is True
    assert dict(api._TAB_GATES)["/api/signal-performance"] == "signal-performance"


def test_shadow_performance_api_matches_login_and_availability_contract(monkeypatch):
    monkeypatch.setattr(api, "HAS_AUTH", True)
    monkeypatch.setattr(
        api,
        "verify_token",
        lambda token: {"email": "pro@example.com", "plan": "pro"}
        if token == "pro-token"
        else None,
        raising=False,
    )
    monkeypatch.setattr(
        api,
        "shadow_summary",
        lambda days=90, mature_only=False: {"window_days": days},
    )

    assert api.api_shadow_signal_performance(
        days=30, authorization="Bearer pro-token"
    )["window_days"] == 30
    with pytest.raises(api.HTTPException) as exc_info:
        api.api_shadow_signal_performance(days=30, authorization=None)
    assert exc_info.value.status_code == 403

    monkeypatch.setattr(
        api,
        "_require_admin",
        lambda authorization: ({"email": "admin@example.com"}, "admin@example.com"),
    )
    monkeypatch.setattr(api, "shadow_summary", None)
    with pytest.raises(api.HTTPException) as unavailable:
        api.api_shadow_signal_performance(
            days=30, authorization="Bearer admin-token"
        )
    assert unavailable.value.status_code == 503


def test_shadow_transitions_and_be_carry_mail_class(tracker):
    tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="SHW", block_reasons="swing_extended_wait_retest")],
        mail_class="shadow")
    d0 = _db_rows("SELECT created_at FROM signals WHERE ticker = 'SHW'")[0]["created_at"][:10]
    from datetime import date, timedelta
    day = date.fromisoformat(d0)
    cursor = day + timedelta(days=1)
    while not st._is_us_equity_session(cursor):
        cursor += timedelta(days=1)

    def fetcher(ticker, since_iso_date):
        # Ein Tag: Open am Entry -> Fill 100, High 106 = MFE +1.2R + TP1-Touch,
        # Low 99.5 ueber dem Stop — Signal bleibt OPEN (kein terminaler Exit,
        # damit die BE-Aktivierung nicht konservativ unterdrueckt wird).
        return [
            {"date": cursor.isoformat(),
             "open": 100.0, "high": 106.0, "low": 99.5, "close": 105.5,
             "interval_complete": True},
        ]

    result = tracker.evaluate_open_signals(stock_daily_fetcher=fetcher)
    assert result["transitions"], "Shadow-Signal muss Transitionen liefern"
    assert all(tr.get("mail_class") == "shadow" for tr in result["transitions"])
    assert result["be_activations"], "BE-Aktivierung erwartet (MFE +1.2R >= +1R)"
    assert all(act.get("mail_class") == "shadow" for act in result["be_activations"])


# ── 3. bg_service: Shadow loest keine Mails aus ──────────────────────────────
def test_update_mail_skips_shadow_transitions(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_send_email_alert",
                        lambda *args, **kwargs: sent.append(args) or True)
    monkeypatch.setattr(bg_service, "_signal_origin_was_mailed",
                        lambda *args, **kwargs: True)
    recipient = "followup@example.com"
    recipient_key = bg_service._recipient_delivery_key(recipient)
    monkeypatch.setattr(
        bg_service,
        "_followup_recipient_profiles",
        lambda _secrets: [{"email": recipient, "position_update_scope": "all",
                           "personal_positions": []}],
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {recipient},
    )
    transitions = [
        {"id": 1, "ticker": "SHW", "scanner": "stock_strategy",
         "mail_class": "shadow", "new_status": "STOP_HIT", "r_realized": -1.0},
        {"id": 2, "ticker": "REAL", "scanner": "stock_strategy",
         "mail_class": "trade", "new_status": "STOP_HIT", "r_realized": -1.0,
         "delivery_recipient_keys": [recipient_key]},
    ]
    assert bg_service._send_signal_update_mail(transitions, None) is True
    assert len(sent) == 1
    body = sent[0][1]
    assert "REAL" in body
    assert "SHW" not in body


def test_update_mail_shadow_only_sends_nothing(monkeypatch):
    sent = []
    monkeypatch.setattr(bg_service, "_send_email_alert",
                        lambda *args, **kwargs: sent.append(args) or True)
    monkeypatch.setattr(bg_service, "_signal_origin_was_mailed",
                        lambda *args, **kwargs: True)
    transitions = [
        {"id": 1, "ticker": "SHW", "scanner": "stock_strategy",
         "mail_class": "shadow", "new_status": "TP2_HIT", "r_realized": 2.0},
    ]
    assert bg_service._send_signal_update_mail(transitions, None) is False
    assert sent == []


def test_be_mail_skips_shadow_activations(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(bg_service, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(bg_service, "_send_email_alert",
                        lambda *args, **kwargs: sent.append(args) or True)
    monkeypatch.setattr(bg_service, "_signal_origin_was_mailed",
                        lambda *args, **kwargs: True)
    recipient = "followup@example.com"
    recipient_key = bg_service._recipient_delivery_key(recipient)
    monkeypatch.setattr(
        bg_service,
        "_followup_recipient_profiles",
        lambda _secrets: [{"email": recipient, "position_update_scope": "all",
                           "personal_positions": []}],
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {recipient},
    )
    monkeypatch.setattr(bg_service, "mark_be_alerts_sent", lambda ids: len(list(ids)))
    activations = [
        {"id": 1, "ticker": "SHW", "scanner": "stock_strategy",
         "mail_class": "shadow", "entry": 100.0, "mfe": 1.2},
        {"id": 2, "ticker": "REAL", "scanner": "stock_strategy",
         "mail_class": "trade", "entry": 100.0, "mfe": 1.2,
         "delivery_recipient_keys": [recipient_key]},
    ]
    assert bg_service._send_be_alert_mail(activations, None) is True
    assert len(sent) == 1
    body = sent[0][1]
    assert "REAL" in body
    assert "SHW" not in body


# ── 4. Wochenreport-Sektion ──────────────────────────────────────────────────
def _weekly_summary():
    return {
        "total": {
            "signals": 5, "open": 2, "tp1_hit": 1, "tp2_hit": 1, "stop_hit": 1,
            "expired": 0, "no_fill": 0, "untracked": 0, "decided_signals": 3,
            "win_rate_pct": 66.7, "avg_r": 0.5, "sum_r": 1.5,
            "win_rate_wilson_95": {"lower_pct": 20.0, "upper_pct": 95.0},
            "avg_r_managed_50_50": 0.4, "avg_r_be": None,
            "be_activations": 0, "be_saved": 0, "alerts_per_day": 0.7,
        },
        "per_scanner": {},
        "recent": [],
    }


def test_weekly_report_renders_shadow_section():
    shadow = {
        "total": {"signals": 3, "open": 1, "decided_signals": 2, "wins": 1,
                  "win_rate_pct": 50.0, "avg_r": -0.25, "sum_r": -0.5},
        "per_reason": {"swing_day_move_extended_wait_retest": 2,
                       "swing_extended_wait_retest": 1},
    }
    _, body = bg_service._build_weekly_report_mail(
        _weekly_summary(), watchdog_events=[], shadow=shadow)
    assert "Shadow-Messung" in body
    assert "3 Signale" in body
    assert "-0.25R" in body
    assert "swing_day_move_extended_wait_retest" in body
    assert "Stichprobe" in body  # decided 2 < 30 -> Vorsichts-Hinweis


def test_weekly_report_omits_shadow_section_when_empty():
    _, body = bg_service._build_weekly_report_mail(
        _weekly_summary(), watchdog_events=[],
        shadow={"total": {"signals": 0}, "per_reason": {}})
    assert "Shadow-Messung" not in body
    _, body_none = bg_service._build_weekly_report_mail(
        _weekly_summary(), watchdog_events=[], shadow=None)
    assert "Shadow-Messung" not in body_none


# ── 5. Pipeline: _send_strategy_scan_alerts sammelt Shadow-Rows ──────────────
def _pipeline_stubs(monkeypatch, tmp_path, classified):
    """Gemeinsame Stubs: Markt offen, Qualitaets-Gates ok, classify per Map."""
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {
        "allowed": True, "reason": "unit-test", "session": "US_REGULAR",
    })
    monkeypatch.setattr(api, "_premarket_window_active", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state",
                        lambda scanner, row, strategy_name=None: row)
    monkeypatch.setattr(api, "_stock_breakout_freshness_state",
                        lambda row, daily_close_confirmed_mode=False: row)
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state",
                        lambda *args, **kwargs: (True, None))
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda *args, **kwargs: ({"GATED", "BASE", "FINE"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    api._EMAIL_COOLDOWN.clear()

    def fake_classify(scanner_name, row, now=None):
        return classified[row["Ticker"]]

    monkeypatch.setattr(api, "_classify_alert_candidate", fake_classify)


def test_pipeline_logs_shadow_for_timing_gated_rows(tmp_path, monkeypatch, tracker):
    sent_mails = []

    def fake_send(*args, **kwargs):
        sent_mails.append(args)
        tracking_rows = list(kwargs.get("tracking_rows") or [])
        tracking_scanner = kwargs.get("tracking_scanner") or ""
        mail_channel = kwargs.get("mail_channel") or ""
        intent_key = tracker.build_alert_delivery_intent_key(
            tracking_scanner,
            tracking_rows,
            channel="email",
            mail_channel=mail_channel,
        )
        prepared = tracker.prepare_alert_delivery_intent(
            tracking_scanner,
            tracking_rows,
            intent_key,
            channel="email",
            mail_channel=mail_channel,
        )
        assert prepared["prepared"] is True
        assert tracker.finalize_alert_delivery(
            intent_key, ["a" * 64]
        )["activated"] is True
        api._set_last_delivery_recipients(["operator@example.com"])
        return True

    monkeypatch.setattr(api, "_send_email_alert", fake_send)
    monkeypatch.setattr(api, "_record_email_event", lambda *args, **kwargs: None)
    _pipeline_stubs(monkeypatch, tmp_path, {
        "GATED": {
            "alertable_now": False,
            "suppression_reasons": ["swing_day_move_extended_wait_retest"],
            "cooldown_key": "stock_strategy_GATED", "ticker": "GATED",
            "grade": "A", "score": 90, "price": 10.0, "rvol": 2.0,
        },
        "BASE": {
            "alertable_now": False,
            "suppression_reasons": ["score_below_alert_threshold"],
            "cooldown_key": "stock_strategy_BASE", "ticker": "BASE",
            "grade": "B", "score": 70, "price": 10.0, "rvol": 2.0,
        },
        "FINE": {
            "alertable_now": True, "suppression_reasons": [],
            "cooldown_key": "stock_strategy_FINE", "ticker": "FINE",
            "grade": "A", "score": 90, "price": 10.0, "rvol": 2.0,
        },
    })

    rows = [
        {"Ticker": "GATED", "grade": "A", "score": 90, "RVOL": 2.0,
         "Preis": 10.0, "change_pct": 5.0, "Signal_Direction": "LONG",
         "Strategy": "Momentum Breakout Long",
         "Entry": 10.0, "StopLoss": 9.5, "TP1": 10.75, "TP2": 11.0,
         "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.5,
                         "tp1": 10.75, "tp2": 11.0}},
        {"Ticker": "BASE", "grade": "B", "score": 70, "RVOL": 2.0,
         "Preis": 10.0, "Signal_Direction": "LONG",
         "Strategy": "Momentum Breakout Long",
         "Entry": 10.0, "StopLoss": 9.5, "TP1": 10.75, "TP2": 11.0,
         "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.5,
                         "tp1": 10.75, "tp2": 11.0}},
        {"Ticker": "FINE", "grade": "A", "score": 90, "RVOL": 2.0,
         "Preis": 10.0, "change_pct": 1.0, "Signal_Direction": "LONG",
         "Strategy": "Momentum Breakout Long",
         "Entry": 10.0, "StopLoss": 9.5, "TP1": 10.75, "TP2": 11.0,
         "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.5,
                         "tp1": 10.75, "tp2": 11.0}},
    ]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    # Genau EINE Mail mit nur FINE (GATED/BASE geblockt) ...
    assert len(sent_mails) == 1
    assert "FINE" in sent_mails[0][1]
    assert "GATED" not in sent_mails[0][1]
    # ... und die Tracker-DB enthaelt: FINE als trade, GATED als shadow,
    # BASE gar nicht (Base-Blocker => keine Shadow-Messung).
    db = {r["ticker"]: r for r in _db_rows()}
    assert db["FINE"]["mail_class"] == "trade"
    assert db["GATED"]["mail_class"] == "shadow"
    assert db["GATED"]["block_reasons"] == "swing_day_move_extended_wait_retest"
    assert "BASE" not in db
    # Trade-Statistik sieht nur FINE.
    summary = tracker.load_performance_summary(days=90)
    assert summary["total"]["signals"] == 1


def test_pipeline_shadow_logged_even_without_any_mail(tmp_path, monkeypatch, tracker):
    """Geht keine Mail raus (alle Rows geblockt), wird das Shadow-Signal
    TROTZDEM geloggt — die Messung ist bewusst mail-unabhaengig."""
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_record_email_event", lambda *args, **kwargs: None)
    _pipeline_stubs(monkeypatch, tmp_path, {
        "GATED": {
            "alertable_now": False,
            "suppression_reasons": ["swing_multi_day_exhausted_no_chase"],
            "cooldown_key": "stock_strategy_GATED", "ticker": "GATED",
            "grade": "A", "score": 88, "price": 30.0, "rvol": 3.0,
        },
    })
    rows = [
        {"Ticker": "GATED", "grade": "A", "score": 88, "RVOL": 3.0,
         "Preis": 30.0, "change_pct": 4.0, "Signal_Direction": "LONG",
         "Strategy": "Momentum Breakout Long",
         "Entry": 30.0, "StopLoss": 28.5, "TP1": 32.0, "TP2": 33.0,
         "trade_setup": {"direction": "LONG", "entry": 30.0, "stop": 28.5,
                         "tp1": 32.0, "tp2": 33.0}},
    ]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    db = _db_rows()
    assert len(db) == 1
    assert db[0]["mail_class"] == "shadow"
    assert db[0]["block_reasons"] == "swing_multi_day_exhausted_no_chase"


# ── 6. Real-Classify: Chase-Gate-Row landet in der Whitelist ─────────────────
def test_real_classify_chase_gated_row_is_shadow_trackable(monkeypatch):
    """Integration ohne Pipeline-Stubs: eine realistische +5%-Row bei 2% ATR
    muss vom echten Gate-Stack NUR mit Whitelist-Gruenden geblockt werden.
    Der Score-Blend (Health-Pfeiler) und die Health-/Barrier-Zusatzgutes
    werden kontrolliert, damit der Test ausschliesslich die Swing-Timing-
    Gates prueft."""
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda *args, **kwargs: ({"CHAS"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason",
                        lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda row, scanner_name: 90)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_structural_barrier_alert_reason", lambda *args, **kwargs: None)
    api._EMAIL_COOLDOWN.clear()
    state = api._classify_alert_candidate("stock_strategy", {
        "Ticker": "CHAS", "grade": "A", "score": 90, "RVOL": 2.0,
        "Preis": 10.0, "current_price": 10.0,
        "change_pct": 5.0, "close_pos": 0.8, "ATR": 0.2,
        "Signal_Direction": "LONG",
        "Strategy": "Momentum Breakout Long",
        "trade_setup": {"direction": "LONG", "entry": 10.0, "stop": 9.4,
                        "tp1": 11.0, "tp2": 12.2},
    })
    assert state["alertable_now"] is False
    assert state["suppression_reasons"], "Row sollte geblockt sein"
    reasons = api._shadow_trackable_reasons("stock_strategy", state, "stocks", False)
    assert reasons == state["suppression_reasons"]
