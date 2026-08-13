"""Tests fuer die Exit-Effizienz-Simulationen (AUDIT 2026-07-29).

Deckt die zwei Gegenprobe-Regeln in modules.signal_tracker ab:
  simulate_breakeven_after_mfe      (Regel A: BE-Stop nach +1R)
  simulate_managed_5050_breakeven   (Regel B: 50/50 + BE-Rest nach TP1)
"""
from modules import signal_tracker as st


def _row(**overrides):
    row = {
        "scanner": "stock_strategy",
        "ticker": "AAA",
        "direction": "LONG",
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 11.0,
        "tp2": 12.0,
        "status": "EXPIRED",
        "r_realized": -0.3,
        "max_favorable_r": 1.4,
        "outcome_detail": "tp1_then_expired",
        "tp1_hit_at": "2026-07-20T15:00:00+00:00",
    }
    row.update(overrides)
    return row


# ── Regel A: Breakeven-Stop nach +mfe_trigger R ────────────────

def test_be_after_mfe_none_without_realized():
    assert st.simulate_breakeven_after_mfe(_row(r_realized=None)) is None


def test_be_after_mfe_untouched_below_trigger():
    assert st.simulate_breakeven_after_mfe(_row(max_favorable_r=0.5, r_realized=-1.0)) == -1.0


def test_be_after_mfe_rescues_loser_to_zero():
    assert st.simulate_breakeven_after_mfe(_row(max_favorable_r=1.2, r_realized=-1.0, outcome_detail="", tp1_hit_at="", status="STOP_HIT")) is None


def test_be_after_mfe_conservative_on_ambiguous_same_day():
    row = _row(max_favorable_r=1.2, r_realized=-1.0, outcome_detail="ambiguous_same_day", status="STOP_HIT")
    assert st.simulate_breakeven_after_mfe(row) == -1.0


def test_be_after_mfe_winner_without_delivery_is_unresolved():
    assert st.simulate_breakeven_after_mfe(_row(r_realized=0.8)) is None


def test_be_after_mfe_missing_mfe_unchanged():
    assert st.simulate_breakeven_after_mfe(_row(max_favorable_r=None, r_realized=-0.4)) == -0.4


def test_be_after_mfe_custom_trigger():
    assert st.simulate_breakeven_after_mfe(_row(max_favorable_r=0.6, r_realized=-0.4), 0.5) is None


# ── Regel B: 50/50 + Breakeven-Rest nach TP1 ───────────────────

def test_5050_be_none_without_realized():
    assert st.simulate_managed_5050_breakeven(_row(r_realized=None)) is None


def test_5050_be_falls_back_to_be_rule_without_tp1():
    # TP1 nie erreicht, aber MFE >= 1R -> BE-Regel rettet auf 0
    row = _row(tp1_hit_at="", status="STOP_HIT", r_realized=-1.0, max_favorable_r=1.3, outcome_detail="")
    assert st.simulate_managed_5050_breakeven(row) is None


def test_5050_be_without_tp1_and_low_mfe_unchanged():
    row = _row(
        tp1_hit_at="",
        status="STOP_HIT",
        r_realized=-1.0,
        max_favorable_r=0.4,
        entry_filled_at="2026-08-13T13:00:00+00:00",
        entry_fill_price=10.0,
    )
    assert st.simulate_managed_5050_breakeven(row) == -1.0


def test_5050_be_after_tp1_expired_negative():
    # Geometrie: entry 10, stop 9.5, tp1 11 -> r_tp1 = 2.0
    # Ist-50/50: 0.5*2.0 + 0.5*(-0.3) = 0.85
    # Regel B:   0.5*2.0 + 0.5*0      = 1.0  (Rest haette bei BE nicht verloren)
    row = _row(r_realized=-0.3)
    base = st._managed_r_50_50(row)
    sim = st.simulate_managed_5050_breakeven(row)
    assert base == 0.85
    assert sim is None


def test_observed_be_gap_is_used_instead_of_invented_zero():
    row = _row(
        r_realized=-1.0,
        tp1_hit_at="",
        status="STOP_HIT",
        be_activated_at="2026-08-13T14:00:00+00:00",
        be_mail_sent_at="2026-08-13T14:01:00+00:00",
        be_exit_at="2026-08-13T14:05:00+00:00",
        be_exit_fill_price=9.9,
        entry_fill_price=10.0,
    )
    assert st.simulate_breakeven_after_mfe(row) == -0.2


def test_5050_be_keeps_tp2_winner():
    row = _row(
        status="TP2_HIT",
        r_realized=3.0,
        outcome_detail="",
        entry_filled_at="2026-08-13T13:00:00+00:00",
        entry_fill_price=10.0,
        be_activated_at="2026-08-13T14:00:00+00:00",
        be_mail_sent_at="2026-08-13T14:01:00+00:00",
    )
    assert st.simulate_managed_5050_breakeven(row) == st._managed_r_50_50(row)


def test_5050_be_keeps_positive_expiry_after_tp1():
    row = _row(
        r_realized=0.6,
        entry_filled_at="2026-08-13T13:00:00+00:00",
        entry_fill_price=10.0,
        be_activated_at="2026-08-13T14:00:00+00:00",
        be_mail_sent_at="2026-08-13T14:01:00+00:00",
    )
    assert st.simulate_managed_5050_breakeven(row) == st._managed_r_50_50(row)


def test_managed_be_undelivered_winner_and_loser_are_both_unresolved():
    winner = _row(status="TP2_HIT", r_realized=3.0, outcome_detail="")
    loser = _row(
        status="STOP_HIT", r_realized=-1.0, tp1_hit_at="", outcome_detail=""
    )

    assert st._managed_5050_be_resolution(winner) == (None, True)
    assert st._managed_5050_be_resolution(loser) == (None, True)


def test_managed_be_unresolved_blocks_reliability_and_verdict():
    row = _row(
        status="TP2_HIT",
        r_realized=3.0,
        outcome_detail="",
        entry_filled_at="2026-08-13T13:00:00+00:00",
        entry_fill_price=10.0,
    )
    bucket = st._performance_bucket_for_rows([dict(row) for _ in range(30)], 90)

    assert bucket["managed_be_decided_signals"] == 0
    assert bucket["managed_be_unresolved"] == 30
    assert bucket["managed_be_sample_reliable"] is False
    assert st.scanner_verdict(bucket)[0] == "beobachten"
