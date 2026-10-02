"""Independent offline review: a display read cannot replace newer worker evidence."""
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import api


@pytest.mark.parametrize("file_age", [600, 2 * 24 * 3600], ids=["fresh-file", "stale-file"])
def test_cache_only_read_preserves_a_newer_worker_publication(monkeypatch, tmp_path, file_age):
    now = time.time()
    cache_path = tmp_path / "common-stock.json"
    old_file = {
        "cached_at": now - file_age, "tickers": ["OLD"],
        "adr_tickers": [], "names": {"OLD": "Old Issuer"},
    }
    cache_path.write_text(json.dumps(old_file), encoding="utf-8")
    memory = {
        "loaded_at": now - 2 * 24 * 3600, "tickers": ["OLD"],
        "source": "old_memory", "adr_tickers": [],
        "names": {"OLD": "Old Issuer"}, "names_refresh_attempted_at": 0,
    }
    monkeypatch.setattr(api, "COMMON_STOCK_UNIVERSE_CACHE", str(cache_path))
    monkeypatch.setattr(api, "_COMMON_STOCK_UNIVERSE_MEM", memory)

    def forbidden_provider(*args, **kwargs):
        raise AssertionError("read-only review must not enter a provider")

    monkeypatch.setattr(api, "rate_limited_get", forbidden_provider)
    captured_old_file, resume_reader = threading.Event(), threading.Event()
    original_load = json.load

    def delayed_load(handle, *args, **kwargs):
        payload = original_load(handle, *args, **kwargs)
        if str(handle.name) == str(cache_path):
            captured_old_file.set()
            assert resume_reader.wait(timeout=2), "review reader was not released"
        return payload

    monkeypatch.setattr(api.json, "load", delayed_load)
    with ThreadPoolExecutor(max_workers=1) as executor:
        reader = executor.submit(api._load_common_stock_universe, require_names=True, cache_only=True)
        try:
            assert captured_old_file.wait(timeout=2), "review did not read the saved file"
            # This is the existing worker publication shape, not a live worker
            # or provider call. The reader has already captured an older file.
            fresh_at = time.time()
            fresh_memory = {
                "loaded_at": fresh_at, "tickers": ["NEW"],
                "source": "polygon_reference", "adr_tickers": [],
                "names": {"NEW": "Fresh Issuer"},
                "names_refresh_attempted_at": fresh_at,
            }
            cache_path.write_text(json.dumps({
                "cached_at": fresh_at, "tickers": ["NEW"],
                "adr_tickers": [], "names": {"NEW": "Fresh Issuer"},
            }), encoding="utf-8")
            memory.update(fresh_memory)
        finally:
            resume_reader.set()
        reader.result(timeout=2)
    assert memory == fresh_memory, "display reader replaced newer worker admission/name evidence"
