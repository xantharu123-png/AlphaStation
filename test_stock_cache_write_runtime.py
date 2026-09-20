"""Offline regressions for cache-write amplification, not wall-clock benchmarks."""
import json
from types import SimpleNamespace

import pytest
import api
from test_stock_momentum_confirmed_contract import _wrapper_fixture, NAME, NOW


def test_slow_partial_write_does_not_trigger_an_immediate_rewrite(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    snapshot = api._fetch_strategy_snapshot_universe(NAME)[0]
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe",
                        lambda *args: [snapshot] + [{"ticker": "INVALID"}] * 80)
    clock = {"now": 100.0}
    monkeypatch.setattr(api, "time", SimpleNamespace(
        monotonic=lambda: clock["now"], time=lambda: NOW.timestamp(), sleep=lambda _: None))
    partials = []

    def slow_write(path, rows, **kwargs):
        partials.append(kwargs["checked"])
        clock["now"] += 2.0  # Saving alone exceeds the 1.5-second publish interval.

    monkeypatch.setattr(api, "save_partial_cache_file", slow_write)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)

    assert len(rows) == 1
    assert partials == [0, 1]  # Initial state + first result, not every rejected symbol.
    assert written[0][1]["metadata"]["diagnostics"]["checked"] == 81


def test_cache_serializes_once_before_one_payload_write(monkeypatch, tmp_path):
    original = api.tempfile.NamedTemporaryFile
    writes = []

    class CountWrites:
        def __init__(self, **kwargs):
            self.file = original(**kwargs)
            self.name = self.file.name

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return self.file.__exit__(*args)

        def write(self, data):
            writes.append(len(data))
            return self.file.write(data)

    monkeypatch.setattr(api.tempfile, "NamedTemporaryFile", CountWrites)
    rows = [{"ticker": "OFFLINE", "zones": [{"lower": i, "upper": i + 1} for i in range(100)]}]
    path = tmp_path / "cache.json"
    api.save_cache_file(str(path), rows, metadata={"diagnostics": {"coverage": "complete"}})
    payload = json.loads(path.read_text())
    assert payload["results"] == rows
    assert payload["diagnostics"] == {"coverage": "complete"}
    assert len(writes) == 1  # Avoid thousands of Python writes for nested zone payloads.


def test_serialization_failure_keeps_previous_cache_and_cleans_temporary(monkeypatch, tmp_path):
    path = tmp_path / "cache.json"
    path.write_text('{"results":["OLD"]}')

    def cannot_encode(value):
        raise TypeError("offline encoding failure")

    monkeypatch.setattr(api, "_serialize_json", cannot_encode)
    with pytest.raises(TypeError, match="offline encoding failure"):
        api.save_cache_file(str(path), [{"unknown": object()}])
    assert json.loads(path.read_text()) == {"results": ["OLD"]}
    assert list(tmp_path.glob("*.tmp")) == []
