"""Deterministic producer input/time-boundary and Biotech parity regressions."""
from datetime import datetime, timezone

import modules.penny_stock_scanner as penny
import modules.premarket as pm
import modules.scanners as scanners
from modules.stock_bars import completed_polygon_bars


def test_penny_rejects_close_outside_high_low():
    impossible = {"open": 100, "high": 101, "low": 99, "close": 150, "volume": 1_000_000}
    assert penny._valid_bars([impossible]) == []


def test_penny_conflicting_duplicate_does_not_pick_last_writer():
    original = {"timestamp": 1_700_000_000, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 500}
    conflict = {**original, "close": 100.5}
    kwargs = {"timeframe_seconds": 300, "now_ts": 1_700_001_000}
    assert penny._completed_bars([original, conflict, original], **kwargs) == []
    assert len(penny._completed_bars([original, original], **kwargs)) == 1


def test_pm_excludes_forming_and_afterhours_opening_bars():
    start, end = 1_700_000_000, 1_700_023_400
    def bar(stamp):
        return {"t": stamp * 1000, "o": 100, "h": 101, "l": 99, "c": 100}
    rows = [bar(end - 600), bar(end - 300), bar(end)]
    assert pm._completed_regular_session_bars(rows, start, end, now_ts=end + 600) == rows[:2]
    assert pm._completed_regular_session_bars(rows, start, end, now_ts=end - 100) == rows[:1]


def test_stock_daily_bar_uses_new_york_close_not_midnight_utc():
    opened = datetime(2026, 9, 4, 4, tzinfo=timezone.utc)
    raw = [{"t": opened.timestamp() * 1000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 500}]
    assert completed_polygon_bars(raw, as_of=datetime(2026, 9, 4, 19, 59, tzinfo=timezone.utc)) == []
    completed = completed_polygon_bars(raw, as_of=datetime(2026, 9, 4, 20, tzinfo=timezone.utc))
    assert len(completed) == 1
    assert completed[0]["date"] == "2026-09-04"


def test_stock_four_hour_future_or_conflicting_bar_cannot_confirm_pattern():
    opened = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    row = {"t": opened.timestamp() * 1000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 500}
    assert completed_polygon_bars([row], span="hour", multiplier=4,
                                   as_of=datetime(2026, 9, 4, 15, tzinfo=timezone.utc)) == []
    assert completed_polygon_bars([row, {**row, "c": 100}], span="hour", multiplier=4,
                                   as_of=datetime(2026, 9, 4, 16, tzinfo=timezone.utc)) == []


def test_biotech_live_and_offline_use_identical_distribution_scoring(monkeypatch):
    raw = []
    start = datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    for index in range(55):
        price = 100 - index * 0.5
        raw.append({"t": (start + index * 86400) * 1000, "o": price + 0.2,
                    "h": price + 1, "l": price - 1, "c": price,
                    "v": 4_000_000 if index == 54 else 500_000})
    class Response:
        status_code = 200
        def json(self):
            return {"results": raw}
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **k: Response())
    canonical = [{"open": b["o"], "high": b["h"], "low": b["l"], "close": b["c"], "volume": b["v"]} for b in raw]
    live = scanners._biotech_technical_score("offline", "TEST")
    offline = scanners._compute_biotech_technical_from_bars(canonical)
    assert live["technical_score"] == offline["technical_score"]
    assert live["rvol"] == offline["rvol"]
    assert "Distribution" in live["details"]["vol_signal"]
    assert live["technical_model"] == offline["technical_model"] == "biotech_completed_bar_v2"
