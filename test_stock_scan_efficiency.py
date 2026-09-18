"""Real historical metrics and cache writes; no provider or SMTP I/O."""
from datetime import datetime, timedelta, timezone
import json

import pytest
import api
from test_stock_momentum_confirmed_contract import _wrapper_fixture, NOW, NAME


def daily_history():
    start = datetime(2026, 3, 1, tzinfo=timezone.utc)
    return [dict(date=(start + timedelta(days=i)).date().isoformat(),
                 open=98., high=100., low=96., close=98., volume=10_000_000)
            for i in range(180)]


def test_rejected_low_volume_candidate_does_not_build_expensive_level_zones(monkeypatch):
    metrics = api._strategy_daily_history_metrics
    builder = api._build_stock_level_snapshot
    written = _wrapper_fixture(monkeypatch)
    monkeypatch.setattr(api, "_strategy_daily_history_metrics", metrics)
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda *args: daily_history())
    monkeypatch.setattr(api, "_stock_previous_session_change", lambda *args, **kw: 0.)
    built = []
    def build(*args, **kwargs):
        built.append(kwargs["symbol"])
        return builder(*args, **kwargs)
    monkeypatch.setattr(api, "_build_stock_level_snapshot", build)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert rows == []
    assert written[0][1]["metadata"]["diagnostics"]["rejected"]["rvol_filter"] == 1
    assert built == []  # Low-volume rejection must happen before zone construction.


def test_metric_screening_keeps_every_non_structure_metric(monkeypatch):
    monkeypatch.setattr(api, "_us_equity_expected_volume_fraction", lambda *args: 1.)
    arguments = dict(price=102., day_open=98., day_high=102., day_low=96.,
                     day_volume=30_000_000, now_utc=NOW, symbol="SYNTHETIC")
    complete = api._strategy_daily_history_metrics(daily_history(), **arguments)
    screening = api._strategy_daily_history_metrics(daily_history(), include_structure=False, **arguments)
    structure_keys = {"support_1", "resistance_1", "level_model", "level_structure", "level_legacy"}
    assert {k: v for k, v in complete.items() if k not in structure_keys} == {
        k: v for k, v in screening.items() if k not in structure_keys}
    assert screening["level_structure"] is None
    assert complete["level_structure"] is not None
    assert screening["rvol20"] == 3.
    assert screening["high_20d"] == 100.


def test_cache_keeps_schema_without_pretty_print_amplification(tmp_path):
    path = tmp_path / "scan.json"
    rows = [{"ticker": "SYNTHETIC", "levels": [{"low": i, "high": i+1} for i in range(50)]}]
    api.save_cache_file(str(path), rows, metadata={"diagnostics": {"complete": True}})
    encoded = path.read_text()
    decoded = json.loads(encoded)
    assert decoded["results"] == rows
    assert decoded["diagnostics"] == {"complete": True}
    assert "cached_at" in decoded
    assert len(encoded) < 1800  # Same payload should not double in size on every partial write.
