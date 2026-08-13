"""Focused regressions for stop execution evidence and performance segmentation."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import bg_service
import modules.signal_tracker as tracker


def _row(ticker="GAP", **overrides):
    row = {
        "Ticker": ticker,
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 105.0,
        "TP2": 110.0,
        "strategy": "Momentum Breakout Long",
        "trade_horizon": "swing",
    }
    row.update(overrides)
    return row


@pytest.fixture()
def isolated_tracker(tmp_path, monkeypatch):
    monkeypatch.setattr(
        tracker, "SIGNAL_DB_PATH", str(tmp_path / "tracker_slippage.sqlite")
    )
    monkeypatch.setenv("APP_REVISION", "aaaaaaaaaaaa")
    return tracker


def _signal(ticker):
    conn = sqlite3.connect(tracker.SIGNAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return dict(
            conn.execute(
                "SELECT * FROM signals WHERE ticker = ? ORDER BY id DESC", (ticker,)
            ).fetchone()
        )
    finally:
        conn.close()


def test_stock_stop_gap_persists_exit_fill_and_separate_slippage(isolated_tracker):
    assert isolated_tracker.record_alert_signals("stock_strategy", [_row()]) == 1
    created = datetime.fromisoformat(_signal("GAP")["created_at"]).astimezone(
        isolated_tracker.ZoneInfo("America/New_York")
    ).date()
    sessions = []
    cursor = created
    while len(sessions) < 2:
        cursor += timedelta(days=1)
        if isolated_tracker._is_us_equity_session(cursor):
            sessions.append(cursor)
    bars = [
        {
            "date": sessions[0].isoformat(),
            "open": 99.0,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "interval_complete": True,
        },
        {
            "date": sessions[1].isoformat(),
            "open": 92.0,
            "high": 94.0,
            "low": 90.0,
            "close": 91.0,
            "interval_complete": True,
        },
    ]

    result = isolated_tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda _ticker, _since: bars
    )

    signal = _signal("GAP")
    assert signal["r_realized"] == pytest.approx(-1.6)
    assert signal["exit_fill_price"] == pytest.approx(92.0)
    assert signal["stop_gap_slippage_r"] == pytest.approx(0.6)
    assert signal["stop_gap_slippage_pct"] == pytest.approx(3.1579)
    transition = result["transitions"][0]
    assert transition["exit_fill_price"] == pytest.approx(92.0)
    assert transition["stop_gap_slippage_r"] == pytest.approx(0.6)
    assert transition["stop_gap_slippage_pct"] == pytest.approx(3.1579)
    assert transition["adverse_slippage_r"] == pytest.approx(0.0)


def test_update_digest_labels_entry_and_stop_gap_slippage_separately():
    transition = {
        "ticker": "GAP",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "new_status": "STOP_HIT",
        "r_realized": -1.6,
        "entry": 100.0,
        "entry_fill_price": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "fill_quality": "OK",
        "adverse_slippage_r": 0.0,
        "adverse_slippage_pct": 0.0,
        "exit_fill_price": 92.0,
        "stop_gap_slippage_r": 0.6,
        "stop_gap_slippage_pct": 3.1579,
    }

    _subject, body = bg_service._build_signal_update_digest(
        [("dedupe", "Stop erreicht", transition)]
    )

    assert "Entry-Slippage +0.00R / +0.00%" in body
    assert "Stop-Exit-Fill $92" in body
    assert "Stop-Gap-Slippage +0.60R / +3.16%" in body
    assert ">Slippage +0.00R" not in body


def test_performance_does_not_count_stale_gap_fields_on_non_stop_rows():
    bucket = tracker._performance_bucket_for_rows(
        [
            {
                "status": tracker.STATUS_NO_FILL,
                "stop_gap_slippage_r": 0.6,
                "exit_fill_price": 92.0,
            }
        ],
        30,
    )

    assert bucket["signals"] == 1
    assert bucket["no_fill"] == 1
    assert bucket["stop_gap_exits"] == 0
    assert bucket["sum_stop_gap_slippage_r"] == 0.0


def test_performance_is_segmented_by_strategy_direction_revision_and_evidence(
    isolated_tracker,
):
    long_row = _row(
        ticker="LONG", strategy="Momentum Breakout Long",
        evaluation_horizon_bars=8, market_regime="GREEN",
    )
    short_row = _row(
        ticker="SHORT",
        Signal_Direction="SHORT",
        StopLoss=105.0,
        TP1=95.0,
        TP2=90.0,
        strategy="Gap Momentum Short",
        evaluation_horizon_bars=3,
        market_regime="RED",
    )
    assert isolated_tracker.record_alert_signals(
        "stock_strategy", [long_row, short_row]
    ) == 2
    as_of = datetime.now(timezone.utc)
    mature_created = (as_of - timedelta(days=30)).isoformat()
    conn = sqlite3.connect(isolated_tracker.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "r_realized_upper=2.0, tp1_hit_at=?, entry_filled_at=?, "
            "entry_fill_price=100.0, created_at=?, code_revision=?, "
            "fill_evidence_mode='verified_snapshot' WHERE ticker='LONG'",
            (mature_created, mature_created, mature_created, "aaaaaaaaaaaa"),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.6, "
            "r_realized_upper=-1.6, entry_filled_at=?, entry_fill_price=100.0, "
            "created_at=?, exit_fill_price=108.0, stop_gap_slippage_r=0.6, "
            "stop_gap_slippage_pct=2.8571, code_revision=?, "
            "fill_evidence_mode='post_alert_interval' WHERE ticker='SHORT'",
            (mature_created, mature_created, "bbbbbbbbbbbb"),
        )
        conn.commit()
    finally:
        conn.close()

    summary = isolated_tracker.load_performance_summary(
        days=30, mature_only=True, as_of=as_of
    )

    assert summary["cohort"] == {
        "mode": "fully_observed",
        "selection_basis": "matured_in_window",
        "mature_only": True,
        "created_in_window": 2,
        "matured_in_window": 2,
        "included_signals": 2,
        "excluded_not_mature": 0,
    }
    assert summary["per_strategy"]["Momentum Breakout Long"]["signals"] == 1
    assert summary["per_strategy"]["Gap Momentum Short"]["signals"] == 1
    assert summary["per_direction"]["LONG"]["sum_r"] == pytest.approx(2.0)
    assert summary["per_direction"]["SHORT"]["sum_r"] == pytest.approx(-1.6)
    assert summary["per_horizon"]["swing:8bars"]["signals"] == 1
    assert summary["per_horizon"]["swing:3bars"]["signals"] == 1
    assert summary["per_market_regime"]["GREEN"]["signals"] == 1
    assert summary["per_market_regime"]["RED"]["signals"] == 1
    assert summary["per_code_revision"]["aaaaaaaaaaaa"]["signals"] == 1
    assert summary["per_code_revision"]["bbbbbbbbbbbb"]["signals"] == 1
    assert summary["per_fill_evidence_mode"]["verified_snapshot"]["signals"] == 1
    assert summary["per_fill_evidence_mode"]["post_alert_interval"]["signals"] == 1
    assert summary["total"]["stop_gap_exits"] == 1
    assert summary["total"]["sum_stop_gap_slippage_r"] == pytest.approx(0.6)
    assert len(summary["segments"]) == 2
    assert summary["calibration_cell_dimensions"] == [
        "scanner", "direction", "horizon", "market_regime",
    ]
    assert len(summary["calibration_cells"]) == 2
    short_cell = next(
        cell for cell in summary["calibration_cells"] if cell["direction"] == "SHORT"
    )
    assert short_cell["cell_id"] == "stock_strategy|SHORT|swing:3bars|RED"
    assert short_cell["verdict"] == "beobachten"
    assert "code_revision" not in short_cell
    assert "fill_evidence_mode" not in short_cell
    short_segment = next(
        segment for segment in summary["segments"] if segment["direction"] == "SHORT"
    )
    assert short_segment["strategy"] == "Gap Momentum Short"
    assert short_segment["scanner"] == "stock_strategy"
    assert short_segment["horizon"] == "swing:3bars"
    assert short_segment["market_regime"] == "RED"
    assert short_segment["code_revision"] == "bbbbbbbbbbbb"
    assert short_segment["fill_evidence_mode"] == "post_alert_interval"
    assert short_segment["sum_r"] == pytest.approx(-1.6)
    assert summary["recent"][0]["code_revision"] in {
        "aaaaaaaaaaaa", "bbbbbbbbbbbb"
    }


def test_public_calibration_identity_matches_persisted_summary_semantics(
    isolated_tracker,
):
    raw_row = _row(
        ticker="CELL-ID",
        Signal_Direction="SHORT",
        StopLoss=105.0,
        TP1=95.0,
        TP2=90.0,
        strategy="Gap Momentum Short",
        trade_horizon="Swing",
    )

    identity = isolated_tracker.build_calibration_cell_identity(
        " Stock_Strategy ", raw_row, market_regime=" risk_off "
    )

    assert identity == {
        "cell_id": "stock_strategy|SHORT|swing:3bars|RISK_OFF",
        "scanner": "stock_strategy",
        "direction": "SHORT",
        "horizon": "swing:3bars",
        "market_regime": "RISK_OFF",
    }
    raw_row["market_regime"] = "risk_off"
    assert isolated_tracker.record_alert_signals(
        "stock_strategy", [raw_row]
    ) == 1
    stored = _signal("CELL-ID")
    assert tuple(identity[key] for key in (
        "scanner", "direction", "horizon", "market_regime"
    )) == isolated_tracker._calibration_cell_key(stored)


def test_public_calibration_identity_is_fail_closed_and_uses_unknown_regime():
    assert tracker.build_calibration_cell_identity("", _row()) is None
    assert tracker.build_calibration_cell_identity("stock_strategy", None) is None
    identity = tracker.build_calibration_cell_identity(
        "stock_strategy", _row(ticker="UNKNOWN-REGIME")
    )
    assert identity["market_regime"] == "UNKNOWN"
    assert identity["cell_id"].endswith("|UNKNOWN")


def test_sixty_bar_maturity_waits_for_exchange_sessions_and_holiday_buffer():
    row = {
        "created_at": "2026-01-01T12:00:00+00:00",
        "asset_class": "stock",
        "evaluation_horizon_bars": 60,
    }

    maturity = tracker._signal_maturity_at(row)

    assert maturity is not None
    assert maturity > datetime(2026, 6, 20, tzinfo=timezone.utc)
    assert tracker._signal_has_full_observation_window(
        row, datetime(2026, 6, 20, tzinfo=timezone.utc)
    ) is False
