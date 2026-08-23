#!/usr/bin/env python3
"""Regime-Filter (F-14, AUDIT 2026-08-01) — Regression-Tests.

Beweist die zwei Layer und ihre Mail-Wirkung:
- Layer 1 MARKET: Mapping market_context-Regime -> GREEN/YELLOW/RED (fail-open)
- Layer 2 BREAKER: Trip (n>=10, ØR<=-0.3, Win<=25%), Release nur durch
  belastbare Post-Trip-Evidenz; Zeitablauf markiert lediglich Review-Bedarf
- Dominanz ROT-Markt > ROT-Breaker > GELB > GREEN; Banner-Inhalte
- api.py-Integration: RED degradiert swing_trade -> watch + Shadow-Tracking
  (send-unabhaengig), Breaker-Watch-Kappe 1/Tag, YELLOW filtert + kappt
- _regime_mail_decision: Env-Schalter, crypto ohne Markt-Gate, State-Persistenz

Session-unabhaengig: alle Zeit-/Markt-/Dedupe-Abhaengigkeiten werden gemockt.
"""
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import api
from modules import regime_filter as rf


UTC = timezone.utc
MON = datetime(2026, 7, 27, 9, 0, tzinfo=UTC)   # Montag
FRI = datetime(2026, 7, 31, 12, 0, tzinfo=UTC)  # Freitag derselben Woche
NXT_MON = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)  # naechster Montag


def _summary(
    decided=0,
    win=0.0,
    avg_r=0.0,
    scanner="stock_strategy",
    direction="LONG",
    horizon="swing:8bars",
    regime="RISK_ON",
    win_upper=None,
    avg_r_upper=None,
):
    cell = {
        "cell_id": f"{scanner}|{direction}|{horizon}|{regime}",
        "scanner": scanner,
        "direction": direction,
        "horizon": horizon,
        "market_regime": regime,
        "decided_signals": decided,
        "win_rate_pct": win,
        "win_rate_pct_upper": win if win_upper is None else win_upper,
        "avg_r": avg_r,
        "avg_r_upper": avg_r if avg_r_upper is None else avg_r_upper,
        "managed_be_decided_signals": decided,
        "managed_be_win_rate_pct": win,
        "managed_be_win_rate_pct_upper": win if win_upper is None else win_upper,
        "avg_r_managed_50_50_be": avg_r,
        "avg_r_managed_50_50_be_upper": avg_r if avg_r_upper is None else avg_r_upper,
        "managed_be_unresolved": 0,
        "control_eligible_signals": decided,
        "control_resolved_signals": decided,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": decided >= 30,
        "managed_be_sample_reliable": decided >= 30,
    }
    return {"per_scanner": {scanner: dict(cell)}, "calibration_cells": [cell]}


def _cell(
    scanner="stock_strategy",
    direction="LONG",
    horizon="swing:8bars",
    regime="RISK_ON",
    *,
    eligible=True,
):
    return {
        "cell_id": f"{scanner}|{direction}|{horizon}|{regime}",
        "scanner": scanner,
        "direction": direction,
        "horizon": horizon,
        "market_regime": regime,
        "trip_release_eligible": eligible,
    }


def _metrics(decided=0, win_pct=0.0, avg_r=0.0, **overrides):
    metrics = {
        "decided": decided,
        "win_pct": win_pct,
        "win_pct_upper": win_pct,
        "avg_r": avg_r,
        "avg_r_upper": avg_r,
        "managed_be_unresolved": 0,
        "control_eligible_signals": 12,
        "control_resolved_signals": 12,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": False,
        "managed_be_sample_reliable": False,
        "control_evidence_complete": True,
        "joint_cell_verified": True,
        "trip_release_eligible": True,
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
    }
    metrics.update(overrides)
    return metrics


def _recovery(decided=30, win_pct=40.0, avg_r=-0.05, **overrides):
    result = {
        "available": True,
        "joint_cell_verified": True,
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
        "managed_be_unresolved": 0,
        "control_eligible_signals": 0,
        "control_resolved_signals": 0,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": False,
        "managed_be_sample_reliable": False,
        "control_evidence_complete": False,
        "decided": decided,
        "win_pct": win_pct,
        "avg_r": avg_r,
        "trade_decided": decided - 2,
        "shadow_decided": 2,
    }
    result.update(overrides)
    return result


# ── Layer 1: Markt-Mapping ───────────────────────────────────────────────────

def test_market_layer_mapping():
    assert rf.market_layer_state({"regime": "PANIC"})["state"] == rf.RED
    assert rf.market_layer_state({"regime": "RISK_OFF"})["state"] == rf.RED
    assert rf.market_layer_state({"regime": "RISK_OFF_LIGHT"})["state"] == rf.YELLOW
    assert rf.market_layer_state({"regime": "NEUTRAL"})["state"] == rf.GREEN
    assert rf.market_layer_state({"regime": "RISK_ON"})["state"] == rf.GREEN
    # fail-open: unbekannt/fehlend darf nie ROT erfinden
    assert rf.market_layer_state({"regime": ""})["state"] == rf.GREEN
    assert rf.market_layer_state(None)["state"] == rf.GREEN
    assert rf.market_layer_state({"regime": "FOO"})["state"] == rf.GREEN


# ── Layer 2: Breaker ─────────────────────────────────────────────────────────

def test_breaker_metrics_extraction():
    m = rf.breaker_metrics(
        _summary(decided=12, win=33.3, avg_r=-0.51),
        "stock_strategy",
        calibration_cell=_cell(),
    )
    assert m == {
        "decided": 12,
        "win_pct": 33.3,
        "win_pct_upper": 33.3,
        "avg_r": -0.51,
        "avg_r_upper": -0.51,
        "managed_be_unresolved": 0,
        "control_eligible_signals": 12,
        "control_resolved_signals": 12,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": False,
        "managed_be_sample_reliable": False,
        "control_evidence_complete": True,
        "joint_cell_verified": True,
        "trip_release_eligible": True,
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
        "r_model": "managed_50_50_plus_breakeven",
    }
    assert rf.breaker_metrics({}, "stock_strategy") == {
        "decided": 0,
        "win_pct": 0.0,
        "win_pct_upper": 0.0,
        "avg_r": 0.0,
        "avg_r_upper": 0.0,
        "managed_be_unresolved": 0,
        "control_eligible_signals": 0,
        "control_resolved_signals": 0,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": False,
        "managed_be_sample_reliable": False,
        "control_evidence_complete": False,
        "joint_cell_verified": False,
        "trip_release_eligible": False,
        "cell_id": None,
        "scanner": None,
        "direction": None,
        "horizon": None,
        "market_regime": None,
        "r_model": "managed_50_50_plus_breakeven",
    }
    assert rf.breaker_metrics(None, "x")["decided"] == 0


def test_breaker_metrics_prefers_recommended_management_model():
    summary = {"calibration_cells": [{
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
        "decided_signals": 20,
        "win_rate_pct": 10.0,
        "avg_r": -0.8,
        "managed_be_decided_signals": 12,
        "managed_be_win_rate_pct": 41.7,
        "managed_be_win_rate_pct_upper": 50.0,
        "avg_r_managed_50_50_be": 0.18,
        "avg_r_managed_50_50_be_upper": 0.42,
        "managed_be_unresolved": 0,
        "control_eligible_signals": 12,
        "control_resolved_signals": 12,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
    }]}

    assert rf.breaker_metrics(
        summary, "stock_strategy", calibration_cell=_cell()
    ) == {
        "decided": 12,
        "win_pct": 41.7,
        "win_pct_upper": 50.0,
        "avg_r": 0.18,
        "avg_r_upper": 0.42,
        "managed_be_unresolved": 0,
        "control_eligible_signals": 12,
        "control_resolved_signals": 12,
        "control_unresolved": 0,
        "control_no_fill": 0,
        "ambiguity_unresolved": 0,
        "ambiguous_outcomes": 0,
        "upper_unresolved": 0,
        "sample_reliable": False,
        "managed_be_sample_reliable": False,
        "control_evidence_complete": True,
        "joint_cell_verified": True,
        "trip_release_eligible": True,
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
        "r_model": "managed_50_50_plus_breakeven",
    }


def test_breaker_trip_exact_boundaries():
    # exakt auf den Schwellen => Trip (<=)
    ev = rf.evaluate_breaker(_metrics(10, 25.0, -0.3), None, MON)
    assert ev["state"] == rf.RED and ev["tripped_at"] == MON.isoformat()
    assert "breaker_trip" in ev["reason"]
    # jede Bedingung einzeln verhindert den Trip
    assert rf.evaluate_breaker(_metrics(9, 0.0, -2.0), None, MON)["state"] == rf.GREEN
    assert rf.evaluate_breaker(_metrics(10, 25.1, -0.5), None, MON)["state"] == rf.GREEN
    assert rf.evaluate_breaker(_metrics(10, 20.0, -0.29), None, MON)["state"] == rf.GREEN


def test_breaker_does_not_trip_on_ambiguous_ohlc_ordering_alone():
    uncertain = _metrics(
        12, 8.3, -0.8, win_pct_upper=41.7, avg_r_upper=0.2
    )
    assert rf.evaluate_breaker(uncertain, None, MON)["state"] == rf.GREEN

    unequivocally_bad = _metrics(
        12, 8.3, -0.8, win_pct_upper=16.7, avg_r_upper=-0.4
    )
    result = rf.evaluate_breaker(unequivocally_bad, None, MON)
    assert result["state"] == rf.RED
    assert result["metrics"]["avg_r_upper"] == -0.4


def test_breaker_reads_only_requested_joint_cell_without_scanner_pooling():
    good = _summary(decided=40, win=60.0, avg_r=0.4)["calibration_cells"][0]
    bad = {
        **good,
        "cell_id": "stock_strategy|SHORT|intraday|RISK_OFF",
        "direction": "SHORT",
        "horizon": "intraday",
        "market_regime": "RISK_OFF",
        "managed_be_decided_signals": 12,
        "control_eligible_signals": 12,
        "control_resolved_signals": 12,
        "managed_be_win_rate_pct": 10.0,
        "managed_be_win_rate_pct_upper": 20.0,
        "avg_r_managed_50_50_be": -0.6,
        "avg_r_managed_50_50_be_upper": -0.4,
    }
    metrics = rf.breaker_metrics(
        {"per_scanner": {"stock_strategy": {"decided_signals": 52}},
         "calibration_cells": [good, bad]},
        "stock_strategy",
        calibration_cell=_cell(
            direction="SHORT", horizon="intraday", regime="RISK_OFF"
        ),
    )
    assert metrics["cell_id"] == bad["cell_id"]
    assert rf.evaluate_breaker(metrics, None, MON)["state"] == rf.RED


def test_breaker_never_trips_from_scanner_aggregate_or_unresolved_cell():
    aggregate_only = {"per_scanner": {"stock_strategy": {
        "managed_be_decided_signals": 100,
        "managed_be_win_rate_pct": 0.0,
        "avg_r_managed_50_50_be": -2.0,
    }}}
    assert rf.breaker_metrics(aggregate_only, "stock_strategy")[
        "joint_cell_verified"
    ] is False
    assert rf.decide_mail_regime(
        "stock_strategy", summary=aggregate_only, state={}, now=MON
    )["state"] == rf.GREEN

    unresolved = _summary(decided=40, win=0.0, avg_r=-2.0)
    unresolved["calibration_cells"][0]["managed_be_unresolved"] = 1
    assert rf.decide_mail_regime(
        "stock_strategy", summary=unresolved, state={}, now=MON,
        calibration_cell=_cell(),
    )["state"] == rf.GREEN


def test_unknown_legacy_unspecified_or_inferred_cells_never_control_breaker():
    bad = _summary(decided=60, win=0.0, avg_r=-2.0)
    for cell in (
        _cell(regime="UNKNOWN"),
        _cell(regime="LEGACY_UNKNOWN"),
        _cell(horizon="unspecified", regime="RISK_ON"),
        _cell(regime="RISK_ON", eligible=False),
    ):
        assert rf.calibration_cell_eligible(cell) is False
        decision = rf.decide_mail_regime(
            "stock_strategy",
            context={"regime": "RISK_ON"},
            summary=bad,
            state={},
            now=MON,
            calibration_cell=cell,
        )
        assert decision["state"] == rf.GREEN
        assert decision["breaker_evaluated"] is False
        assert decision["new_state_entry"] is None


def test_combined_gate_colors_are_never_valid_market_regime_cells():
    for gate_color in (rf.GREEN, rf.YELLOW, rf.RED, "GATE_DISABLED"):
        assert rf.calibration_cell_eligible(
            _cell(regime=gate_color)
        ) is False


def test_bad_cell_does_not_degrade_a_different_good_cell():
    bad = _summary(decided=30, win=0.0, avg_r=-1.0)["calibration_cells"][0]
    good = {
        **bad,
        **_cell(direction="SHORT", regime="RISK_ON"),
        "managed_be_decided_signals": 40,
        "control_eligible_signals": 40,
        "control_resolved_signals": 40,
        "managed_be_win_rate_pct": 60.0,
        "managed_be_win_rate_pct_upper": 70.0,
        "avg_r_managed_50_50_be": 0.4,
        "avg_r_managed_50_50_be_upper": 0.6,
    }
    summary = {"calibration_cells": [bad, good]}
    bad_decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_ON"}, summary=summary,
        state={}, now=MON, calibration_cell=_cell(),
    )
    good_decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_ON"}, summary=summary,
        state={}, now=MON,
        calibration_cell=_cell(direction="SHORT", regime="RISK_ON"),
    )
    assert bad_decision["state"] == rf.RED
    assert good_decision["state"] == rf.GREEN
    assert bad_decision["state_key"] != good_decision["state_key"]


def test_breaker_cooldown_persists_and_recovers():
    entry = {"tripped_at": MON.isoformat(), **_cell()}
    # naechster Tag, weiter schlecht => COOLDOWN haelt, Trip-Zeit bleibt
    ev = rf.evaluate_breaker({"decided": 12, "win_pct": 10.0, "avg_r": -0.5}, entry,
                             MON + timedelta(days=1))
    assert ev["state"] == rf.RED and ev["tripped_at"] == MON.isoformat()
    assert "breaker_cooldown" in ev["reason"]
    # Nur echte Post-Trip-Erholung => Release, Trip geloescht
    ev2 = rf.evaluate_breaker(
        _metrics(12, 40.0, -0.05),
        entry,
        MON + timedelta(days=2),
        recovery_metrics=_recovery(),
    )
    assert ev2["state"] == rf.GREEN and ev2["tripped_at"] is None
    assert "breaker_release_recovered" in ev2["reason"]
    assert ev2["recovery_metrics"]["trade_decided"] == 28
    assert ev2["recovery_metrics"]["shadow_decided"] == 2


def test_breaker_stays_blocked_after_5_trading_days_without_recovery_evidence():
    entry = {"tripped_at": MON.isoformat()}
    bad = {"decided": 12, "win_pct": 0.0, "avg_r": -0.6}
    # Freitag derselben Woche: erst 4 Werktage => haelt
    assert rf.evaluate_breaker(bad, entry, FRI)["state"] == rf.RED
    # Naechster Montag: Review ist faellig, aber Zeit allein darf nie freigeben.
    ev = rf.evaluate_breaker(bad, entry, NXT_MON)
    assert ev["state"] == rf.RED and ev["tripped_at"] == MON.isoformat()
    assert ev["review_due"] is True
    assert "breaker_review_due" in ev["reason"]


def test_breaker_ignores_recovered_rolling_window_without_post_trip_evidence():
    entry = {"tripped_at": MON.isoformat()}
    recovered_rolling_window = {"decided": 20, "win_pct": 55.0, "avg_r": 0.4}
    ev = rf.evaluate_breaker(
        recovered_rolling_window,
        entry,
        NXT_MON,
        recovery_metrics={"available": False, "error": "db unavailable"},
    )
    assert ev["state"] == rf.RED
    assert ev["review_due"] is True
    assert ev["recovery_metrics"]["error"] == "db unavailable"


def test_breaker_requires_minimum_post_trip_sample_before_release():
    entry = {"tripped_at": MON.isoformat()}
    ev = rf.evaluate_breaker(
        {"decided": 20, "win_pct": 60.0, "avg_r": 0.5},
        entry,
        NXT_MON,
        recovery_metrics={
            "available": True,
            "joint_cell_verified": True,
            "managed_be_unresolved": 0,
            "decided": 29,
            "win_pct": 75.0,
            "avg_r": 0.8,
            "trade_decided": 27,
            "shadow_decided": 2,
        },
    )
    assert ev["state"] == rf.RED
    assert ev["review_due"] is True
    assert ev["recovery_metrics"]["decided"] == 29


def test_breaker_release_requires_exact_exogenous_cell_and_zero_unresolved():
    entry = {"tripped_at": MON.isoformat(), **_cell()}
    wrong_regime = _recovery(market_regime="RISK_OFF")
    wrong_regime["cell_id"] = "stock_strategy|LONG|swing:8bars|RISK_OFF"
    unresolved = _recovery(managed_be_unresolved=1)
    for evidence in (wrong_regime, unresolved):
        result = rf.evaluate_breaker(
            _metrics(30, 50.0, 0.2), entry, NXT_MON,
            recovery_metrics=evidence,
        )
        assert result["state"] == rf.RED
        assert result["tripped_at"] == MON.isoformat()


def test_trading_days_between_edges():
    assert rf.trading_days_between(MON.date(), MON.date()) == 0
    assert rf.trading_days_between(FRI.date(), (FRI + timedelta(days=2)).date()) == 0  # Sa+So
    assert rf.trading_days_between(FRI.date(), (FRI + timedelta(days=3)).date()) == 1  # +Mo
    assert rf.trading_days_between(MON.date(), NXT_MON.date()) == 5
    assert rf.trading_days_between(None, NXT_MON.date()) == 0


def test_state_roundtrip_and_corrupt(tmp_path):
    path = tmp_path / "regime_state.json"
    assert rf.load_state(path) == {}                      # fehlend => {}
    assert rf.save_state({"breakers": {"x": {"tripped_at": "t"}}}, path) is True
    assert rf.load_state(path)["breakers"]["x"]["tripped_at"] == "t"
    path.write_text("{kaputt", encoding="utf-8")
    assert rf.load_state(path) == {}                      # korrupt => {}


def test_state_atomic_update_keeps_twenty_parallel_breaker_cells(tmp_path):
    path = tmp_path / "regime_state.json"

    def _store_cell(index):
        cell_id = f"scanner-{index}|LONG|swing:8bars|RISK_ON"

        def _mutate(state):
            breakers = dict(state.get("breakers") or {})
            breakers[cell_id] = {"tripped_at": MON.isoformat(), "cell_id": cell_id}
            state["breakers"] = breakers

        return rf.update_state(_mutate, path)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(_store_cell, range(20)))

    persisted = rf.load_state(path)
    assert results == [True] * 20
    assert len(persisted.get("breakers") or {}) == 20
    assert set(persisted["breakers"]) == {
        f"scanner-{index}|LONG|swing:8bars|RISK_ON" for index in range(20)
    }
    assert not list(tmp_path.glob(".regime_state.json.*.tmp"))


# ── Kombinierte Entscheidung ─────────────────────────────────────────────────

def test_decide_dominance_and_state_entry():
    # Markt ROT dominiert Breaker
    d = rf.decide_mail_regime("stock_strategy",
                              context={"regime": "RISK_OFF", "overall_risk_score": 70},
                              summary=_summary(decided=12, win=0.0, avg_r=-0.6),
                              state={}, now=MON,
                              calibration_cell=_cell())
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_MARKET
    assert d["reason_tag"] == rf.REASON_MARKET_RED and "🟥" in d["banner"]
    # Breaker ROT, wenn Markt gruen — Trip-Zeit wird zur Persistierung gereicht
    d2 = rf.decide_mail_regime("stock_strategy",
                               context={"regime": "RISK_ON"},
                               summary=_summary(decided=12, win=10.0, avg_r=-0.5),
                               state={}, now=MON,
                               calibration_cell=_cell())
    assert d2["state"] == rf.RED and d2["layer"] == rf.LAYER_BREAKER
    assert d2["reason_tag"] == rf.REASON_BREAKER_COOLDOWN
    assert d2["new_state_entry"] == {
        "tripped_at": MON.isoformat(),
        "cell_id": "stock_strategy|LONG|swing:8bars|RISK_ON",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "horizon": "swing:8bars",
        "market_regime": "RISK_ON",
    }
    assert d2["watch_cap_seconds"] == 20 * 3600
    # GELB mit Verschaerfungsparametern
    d3 = rf.decide_mail_regime("stock_strategy",
                               context={"regime": "RISK_OFF_LIGHT"},
                               summary=_summary(), state={}, now=MON,
                               calibration_cell=_cell())
    assert d3["state"] == rf.YELLOW and d3["score_boost"] == 5 and d3["max_rows"] == 2
    assert "🟨" in d3["banner"]
    # GREEN ohne Banner/Tag
    d4 = rf.decide_mail_regime("stock_strategy", context={"regime": "NEUTRAL"},
                               summary=_summary(decided=30, win=50.0, avg_r=0.4),
                               state={}, now=MON,
                               calibration_cell=_cell())
    assert d4["state"] == rf.GREEN and d4["banner"] == "" and d4["reason_tag"] == ""
    # Schalter aus => GREEN
    d5 = rf.decide_mail_regime("stock_strategy", context={"regime": "PANIC"},
                               summary=_summary(decided=12, win=0.0, avg_r=-0.6),
                               state={}, now=MON,
                               calibration_cell=_cell(),
                               market_gate_enabled=False, breaker_enabled=False)
    assert d5["state"] == rf.GREEN


def test_market_red_is_long_only_for_an_explicit_valid_short_cell():
    short_cell = _cell(direction="SHORT", regime="RISK_OFF")
    short_decision = rf.decide_mail_regime(
        "stock_strategy",
        context={"regime": "RISK_OFF", "overall_risk_score": 70},
        summary=None,
        state={},
        now=MON,
        calibration_cell=short_cell,
    )
    assert short_decision["market"]["state"] == rf.RED
    assert short_decision["market_red_applies"] is False
    assert short_decision["state"] == rf.GREEN
    assert short_decision["breaker_evaluated"] is True

    bad_short = rf.decide_mail_regime(
        "stock_strategy",
        context={"regime": "RISK_OFF", "overall_risk_score": 70},
        summary=_summary(
            decided=12,
            win=10.0,
            avg_r=-0.5,
            direction="SHORT",
            regime="RISK_OFF",
        ),
        state={},
        now=MON,
        calibration_cell=short_cell,
    )
    assert bad_short["state"] == rf.RED
    assert bad_short["layer"] == rf.LAYER_BREAKER

    long_decision = rf.decide_mail_regime(
        "stock_strategy",
        context={"regime": "PANIC", "overall_risk_score": 81},
        summary=None,
        state={},
        now=MON,
        calibration_cell=_cell(direction="LONG", regime="PANIC"),
    )
    assert long_decision["market_red_applies"] is True
    assert long_decision["state"] == rf.RED
    assert long_decision["layer"] == rf.LAYER_MARKET

    # A direction label alone is not enough: an ineligible/missing dimension
    # must retain the previous fail-closed market-RED behaviour.
    invalid_short = rf.decide_mail_regime(
        "stock_strategy",
        context={"regime": "RISK_OFF"},
        summary=None,
        state={},
        now=MON,
        calibration_cell=_cell(
            direction="SHORT", regime="RISK_OFF", eligible=False
        ),
    )
    assert invalid_short["market_red_applies"] is True
    assert invalid_short["state"] == rf.RED


def test_banners_carry_facts():
    d = rf.decide_mail_regime("stock_strategy", context={"regime": "PANIC", "overall_risk_score": 81},
                              summary=None, state={}, now=MON)
    assert "PANIC" in d["banner"] and "81" in d["banner"] and "NUR BEOBACHTUNG" in d["banner"]
    d2 = rf.decide_mail_regime("stock_strategy", context=None,
                               summary=_summary(decided=12, win=10.0, avg_r=-0.5),
                               state={}, now=MON,
                               calibration_cell=_cell())
    assert "COOLDOWN" in d2["banner"] and "stock_strategy" in d2["banner"]


# ── api.py-Integration: _regime_mail_decision ────────────────────────────────

def test_api_decision_market_red_and_state_path(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot",
                        lambda: {"regime": "RISK_OFF", "overall_risk_score": 68})
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda days=30, mature_only=False: _summary(),
        raising=False,
    )
    d = api._regime_mail_decision(
        "stock_strategy", "stocks", False, MON, calibration_row=_row("AAA")
    )
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_MARKET


def test_api_decision_env_kill_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.setenv("REGIME_FILTER_ENABLED", "0")
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "PANIC"})
    assert api._regime_mail_decision("stock_strategy", "stocks", False, MON) is None


def test_api_decision_breaker_trip_persists_state(monkeypatch, tmp_path):
    state_path = tmp_path / "regime_state.json"
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", state_path)
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "RISK_ON"})
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda days=30, mature_only=False: _summary(
            decided=12, win=10.0, avg_r=-0.5
        ),
        raising=False,
    )
    d = api._regime_mail_decision(
        "stock_strategy", "stocks", False, MON, calibration_row=_row("AAA")
    )
    assert d["state"] == rf.RED and d["layer"] == rf.LAYER_BREAKER
    persisted = rf.load_state(state_path)
    assert persisted["breakers"][
        "stock_strategy|LONG|swing:8bars|RISK_ON"
    ]["tripped_at"] == MON.isoformat()


def test_api_breaker_recovery_is_pinned_to_persisted_joint_cell(
    monkeypatch, tmp_path
):
    state_path = tmp_path / "regime_state.json"
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", state_path)
    cell = "stock_strategy|SHORT|intraday:1bars|RISK_ON"
    rf.save_state({"breakers": {cell: {
        "tripped_at": MON.isoformat(),
        "cell_id": cell,
        "scanner": "stock_strategy",
        "direction": "SHORT",
        "horizon": "intraday:1bars",
        "market_regime": "RISK_ON",
    }}})
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "RISK_ON"})
    monkeypatch.setattr(
        api, "load_performance_summary",
        lambda days=30, mature_only=True: {
            "calibration_cells": [{
                **_summary(decided=40, win=60.0, avg_r=0.4)["calibration_cells"][0],
                "cell_id": cell,
                "direction": "SHORT",
                "horizon": "intraday:1bars",
                "market_regime": "RISK_ON",
            }]
        },
    )
    calls = []
    _recovery_fixture = _recovery()
    _recovery_fixture.update({
        "cell_id": cell,
        "direction": "SHORT",
        "horizon": "intraday:1bars",
        "market_regime": "RISK_ON",
    })

    def _load_recovery(scanner, since, direction=None, horizon=None, market_regime=None):
        calls.append((scanner, since, direction, horizon, market_regime))
        return _recovery_fixture
    monkeypatch.setattr(api, "load_breaker_recovery_summary", _load_recovery)

    decision = api._regime_mail_decision(
        "stock_strategy",
        "stocks",
        False,
        NXT_MON,
        calibration_row=_row("AAA", direction="SHORT", horizon="intraday"),
    )
    assert decision["state"] == rf.GREEN
    assert calls == [
        ("stock_strategy", MON.isoformat(), "SHORT", "intraday:1bars", "RISK_ON")
    ]


def test_api_decision_crypto_has_no_market_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"regime": "PANIC"})
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda days=30, mature_only=False: _summary(scanner="crypto_strategy"),
        raising=False,
    )
    # PANIC gilt nicht fuer Crypto; Breaker ohne Daten => GREEN. GREEN bleibt
    # als Messkontext erhalten, damit die Tracker-Zelle nicht UNKNOWN wird.
    decision = api._regime_mail_decision("crypto_strategy", "crypto", False, MON)
    assert decision["state"] == "GREEN"
    assert decision["banner"] == ""
    # PM-Radar wird nie vom Regime-Filter angefasst
    assert api._regime_mail_decision("stock_strategy", "stocks", True, MON) is None


def test_api_rejects_inferred_dimensions_and_combined_gate_regime(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.setattr(
        api, "_get_market_context_snapshot", lambda: {"regime": "RISK_ON"}
    )
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda days=30, mature_only=True: _summary(
            decided=60, win=0.0, avg_r=-2.0
        ),
    )
    inferred = _row("AAA")
    inferred.pop("Signal_Direction")
    inferred.pop("trade_horizon")
    inferred["trade_setup"].pop("direction")
    decision = api._regime_mail_decision(
        "stock_strategy", "stocks", False, MON, calibration_row=inferred
    )
    assert decision["state"] == rf.GREEN
    assert decision["breaker_evaluated"] is False
    assert rf.load_state(tmp_path / "regime_state.json") == {}


def test_api_tracker_rows_keep_exogenous_regime_not_gate_color(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda row, scanner: 90)
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda row, scanner: [])

    def _decision(*_args, calibration_row=None, **_kwargs):
        return {
            "state": rf.GREEN,
            "layer": None,
            "banner": "",
            "reason_tag": "",
            "market": {"regime": "NEUTRAL"},
            "calibration_cell": _cell(regime="NEUTRAL"),
        }

    monkeypatch.setattr(api, "_regime_mail_decision", _decision)
    api._send_strategy_scan_alerts(
        "Aktien Auto-Sweep", [_row("AAA")], "stocks"
    )
    assert sent[0]["tracking_rows"][0]["market_regime"] == "NEUTRAL"
    assert sent[0]["tracking_rows"][0]["market_regime"] != rf.GREEN


# ── api.py-Integration: Mail-Pfad ────────────────────────────────────────────

class _FakeDedupe:
    def __init__(self):
        self.claimed = {}
        self.released = []

    def claim(self, key, ttl, now=None):
        last = self.claimed.get(key)
        if last is not None and ((now or 0) - last) < ttl:
            return False
        self.claimed[key] = now if now is not None else 0
        return True

    def remaining(self, key, ttl, now=None):
        last = self.claimed.get(key)
        if last is None:
            return 0
        return max(0, ttl - ((now or 0) - last))

    def release(self, key, claimed_at=None):
        self.released.append(key)
        self.claimed.pop(key, None)

    def mark(self, key, now=None):
        self.claimed[key] = now if now is not None else 0


def _row(ticker, score=90.0, direction="LONG", horizon="swing"):
    return {
        "Ticker": ticker, "grade": "A", "score": score, "RVOL": 2.0,
        "Preis": 10.0, "current_price": 10.0, "change_pct": 3.5, "close_pos": 0.8,
        "Signal_Direction": direction,
        "trade_horizon": horizon,
        "trade_setup": {"direction": direction, "entry": 10.0, "stop": 9.5,
                        "tp1": 10.75, "tp2": 11.0},
    }


def _mail_harness(monkeypatch):
    """Gemeinsames Geruest: Send/Track/Event aufgezeichnet, Dedupe im Speicher,
    US-Session offen, PM-Fenster aus."""
    sent, tracked, events = [], [], []
    dedupe = _FakeDedupe()
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_send_email_alert",
                        lambda subject, body, **kw: sent.append({"subject": subject, "body": body, **kw}) or True)
    monkeypatch.setattr(api, "_safe_record_alert_signals",
                        lambda scanner, rows, mail_class="trade", channel="email":
                        tracked.append({"scanner": scanner, "rows": rows,
                                        "mail_class": mail_class, "channel": channel}))
    monkeypatch.setattr(api, "_record_email_event",
                        lambda subject, status, reason=None: events.append((subject, status, reason)))
    monkeypatch.setattr(api, "_email_dedupe_claim", dedupe.claim)
    monkeypatch.setattr(api, "_email_dedupe_remaining", dedupe.remaining)
    monkeypatch.setattr(api, "_email_dedupe_release", dedupe.release)
    monkeypatch.setattr(api, "_email_dedupe_mark", dedupe.mark)
    monkeypatch.setattr(api, "_stock_trade_email_status",
                        lambda *a, **k: {"allowed": True, "session": "US_REGULAR", "reason": ""})
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: False)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"AAA", "BBB", "CCC"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: None)
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    # Keine Marktdaten-Caches im Unit-Test: sonst haengt der Ausgang davon ab,
    # ob zufaellig echte Cache-Dateien (z. B. fuer den echten Ticker "CCC")
    # auf der Platte liegen. Der 4H-Execution-State wird direkt auf CLEAR
    # fixiert (fail-open-Zustand eines ruhigen Marktes).
    monkeypatch.setattr(api, "_fetch_stock_swing_execution_state",
                        lambda ticker: {"Swing_4H_Execution_Checked": True,
                                        "Swing_4H_Execution_Status": "CLEAR",
                                        "Swing_4H_Execution_Reason": "unit_test"})
    return sent, tracked, events, dedupe


def test_mail_path_red_degrades_to_watch_and_shadow(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_OFF", "overall_risk_score": 70},
        summary=None, state={}, now=MON)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA"), _row("BBB")], "stocks")

    assert len(sent) == 1
    assert sent[0]["mail_class"] == "watch"                       # degradiert
    assert "🟥 MARKT-REGIME ROT" in sent[0]["body"]               # Banner sichtbar
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    trades = [t for t in tracked if t["mail_class"] == "trade"]
    assert trades == []                                           # kein Trade-Tracking
    assert len(shadow) == 1
    reasons = {r.get("block_reasons") for r in shadow[0]["rows"]}
    assert reasons == {rf.REASON_MARKET_RED}
    assert len(shadow[0]["rows"]) == 2                            # beide Setups gemessen


def test_mail_path_red_breaker_daily_cap_skips_mail(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context=None,
        summary=_summary(decided=12, win=10.0, avg_r=-0.5), state={}, now=MON,
        calibration_cell=_cell())
    assert decision["layer"] == rf.LAYER_BREAKER
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)
    # Watch-Kappe bereits verbraucht (vor < 20h verschickt)
    import time as _time
    import hashlib
    _cap_key = "regime_cooldown_watch_" + hashlib.sha256(
        _cell()["cell_id"].encode("utf-8")
    ).hexdigest()[:20]
    dedupe.claim(_cap_key, 20 * 3600, now=_time.time())

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA")], "stocks")

    assert sent == []                                             # keine Spam-Watch
    assert any(e[1] == "skipped" and "regime_cooldown_watch_cell_cap" in str(e[2]) for e in events)
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    assert len(shadow) == 1 and shadow[0]["rows"][0]["block_reasons"] == rf.REASON_BREAKER_COOLDOWN
    assert any(
        key.startswith("stock_strategy_AAA_") and not key.endswith("__watch")
        for key in dedupe.released
    )                                                               # Claims freigegeben


def test_mail_path_yellow_filters_and_caps(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    decision = rf.decide_mail_regime(
        "stock_strategy", context={"regime": "RISK_OFF_LIGHT"},
        summary=None, state={}, now=MON)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: decision)
    # Die Mail-Pipeline rechnet den Aktien-Score intern als Trade-Health-Score
    # neu (_stock_alert_trade_score); synthetische Rows ohne Detailfelder
    # landen dabei alle auf demselben Default. Fuer den Filter-Test zaehlt nur,
    # dass der YELLOW-Filter gegen DEN Score arbeitet, der auch in der Mail
    # steht — deshalb Passthrough auf den Row-Score.
    monkeypatch.setattr(api, "_stock_alert_trade_score",
                        lambda row, scanner_name: int(row.get("score", 0) or 0))

    rows = [_row("AAA", 90.0), _row("BBB", 84.0), _row("CCC", 82.0)]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert len(sent) == 1
    assert sent[0]["mail_class"] == "swing_trade"                 # GELB bleibt trade-faehig
    assert "🟨 MARKT-REGIME GELB" in sent[0]["body"]
    assert "ab Score 85" in sent[0]["body"]                       # versch. Schwelle gezeigt
    assert "AAA" in sent[0]["body"] and "BBB" not in sent[0]["body"]
    assert sent[0]["tracking_scanner"] == "stock_strategy"
    assert len(sent[0]["tracking_rows"]) == 1                     # nur der Ueberlebende
    shadow = [t for t in tracked if t["mail_class"] == "shadow"]
    assert len(shadow) == 1
    assert {r["block_reasons"] for r in shadow[0]["rows"]} == {rf.REASON_MARKET_YELLOW}
    assert len(shadow[0]["rows"]) == 2                            # BBB + CCC gemessen


def test_mail_path_green_unchanged(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row("AAA")], "stocks")

    assert len(sent) == 1 and sent[0]["mail_class"] == "swing_trade"
    assert "🟥" not in sent[0]["body"] and "🟨" not in sent[0]["body"]
    assert sent[0]["tracking_scanner"] == "stock_strategy"
    assert len(sent[0]["tracking_rows"]) == 1


def test_mail_path_degrades_only_bad_breaker_cell(monkeypatch):
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)

    def _decision(_scanner, _market, _pm, _now, *, calibration_row=None, **_kw):
        direction = str(
            (calibration_row or {}).get("Signal_Direction")
            or ((calibration_row or {}).get("trade_setup") or {}).get("direction")
            or "LONG"
        )
        cell = _cell(direction=direction)
        if direction == "LONG":
            return {
                "state": rf.RED,
                "layer": rf.LAYER_BREAKER,
                "reason_tag": rf.REASON_BREAKER_COOLDOWN,
                "banner": "<p>LONG CELL RED</p>",
                "watch_cap_seconds": 20 * 3600,
                "state_key": cell["cell_id"],
                "calibration_cell": cell,
                "market": {"regime": "RISK_ON"},
            }
        return {
            "state": rf.GREEN,
            "layer": None,
            "reason_tag": "",
            "banner": "",
            "state_key": cell["cell_id"],
            "calibration_cell": cell,
            "market": {"regime": "RISK_ON"},
        }

    monkeypatch.setattr(api, "_regime_mail_decision", _decision)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda row, _scanner: int(row.get("score", 90)))
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_stock_swing_short_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda row, scanner: [])
    rows = [
        _row("AAA", direction="LONG"),
        # Keep geometry valid for the short classifier.
        {
            **_row("BBB", direction="SHORT"),
            "trade_setup": {
                "direction": "SHORT", "entry": 10.0, "stop": 10.5,
                "tp1": 9.25, "tp2": 9.0,
            },
        },
    ]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    watches = [mail for mail in sent if mail["mail_class"] == "watch"]
    trades = [mail for mail in sent if mail["mail_class"] == "swing_trade"]
    assert sent, (events, tracked, dedupe.claimed, dedupe.released)
    assert len(watches) == 1 and "AAA" in watches[0]["body"]
    assert "BBB" not in watches[0]["body"]
    assert len(trades) == 1 and "BBB" in trades[0]["body"]
    assert "AAA" not in trades[0]["body"]
    assert trades[0]["tracking_rows"][0]["market_regime"] == "RISK_ON"
    shadow = [row for item in tracked if item["mail_class"] == "shadow" for row in item["rows"]]
    assert [row["ticker"] for row in shadow] == ["AAA"]
    assert shadow[0]["market_regime"] == "RISK_ON"


def test_mail_path_risk_off_splits_long_watch_from_short_trade(
    monkeypatch, tmp_path
):
    """Real regime integration: one batch must not spread LONG risk to SHORT."""
    sent, tracked, events, dedupe = _mail_harness(monkeypatch)
    monkeypatch.setattr(rf, "DEFAULT_STATE_PATH", tmp_path / "regime_state.json")
    monkeypatch.delenv("REGIME_FILTER_ENABLED", raising=False)
    monkeypatch.delenv("REGIME_MARKET_GATE_ENABLED", raising=False)
    monkeypatch.delenv("REGIME_BREAKER_ENABLED", raising=False)
    monkeypatch.setattr(
        api,
        "_get_market_context_snapshot",
        lambda: {"regime": "RISK_OFF", "overall_risk_score": 73},
    )
    monkeypatch.setattr(
        api,
        "load_performance_summary",
        lambda **_kwargs: _summary(
            decided=30,
            win=60.0,
            avg_r=0.4,
            direction="SHORT",
            horizon="swing:8bars",
            regime="RISK_OFF",
        ),
    )
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)
    monkeypatch.setattr(
        api, "_stock_alert_trade_score", lambda row, _scanner: int(row.get("score", 90))
    )
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_stock_swing_short_rule_reasons", lambda row: [])
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda row, scanner: [])
    monkeypatch.setattr(api, "_adr_ticker_set", lambda: {"AAA", "BBB"})

    long_row = _row("AAA", direction="LONG")
    short_row = {
        **_row("BBB", direction="SHORT"),
        "trade_setup": {
            "direction": "SHORT",
            "entry": 10.0,
            "stop": 10.5,
            "tp1": 9.25,
            "tp2": 9.0,
        },
    }
    api._send_strategy_scan_alerts(
        "Aktien Auto-Sweep", [long_row, short_row], "stocks"
    )

    watches = [mail for mail in sent if mail["mail_class"] == "watch"]
    trades = [mail for mail in sent if mail["mail_class"] == "swing_trade"]
    assert len(watches) == 1
    assert "<b>AAA</b>" in watches[0]["body"]
    assert "<b>BBB</b>" not in watches[0]["body"]
    assert len(trades) == 1
    assert "<b>BBB</b>" in trades[0]["body"]
    assert "<b>AAA</b>" not in trades[0]["body"]
    assert trades[0]["tracking_scanner"] == "stock_strategy"
    assert len(trades[0]["tracking_rows"]) == 1
    assert trades[0]["tracking_rows"][0]["Signal_Direction"] == "SHORT"
    assert trades[0]["tracking_rows"][0]["market_regime"] == "RISK_OFF"

    # The two deliveries are separated, but both still expose the original
    # mixed-batch concentration context.
    assert "Klumpenrisiko: 2 Setups" in watches[0]["body"]
    assert "Klumpenrisiko: 2 Setups" in trades[0]["body"]

    shadow = [
        row
        for item in tracked
        if item["mail_class"] == "shadow"
        for row in item["rows"]
    ]
    assert [row["ticker"] for row in shadow] == ["AAA"]
    assert shadow[0]["block_reasons"] == rf.REASON_MARKET_RED
    assert shadow[0]["market_regime"] == "RISK_OFF"

    watch_key = watches[0]["delivery_dedupe_keys"][0]
    trade_key = trades[0]["delivery_dedupe_keys"][0]
    assert watch_key.endswith("__watch")
    assert not trade_key.endswith("__watch")
    original_long_key = watch_key.removesuffix("__watch")
    assert original_long_key in dedupe.released
    assert original_long_key not in dedupe.claimed
    assert set(dedupe.claimed) == {watch_key, trade_key}
