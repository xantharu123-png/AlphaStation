"""A denied optional catalyst account must not flood a whole stock scan."""
import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from modules import data_fetchers as data


@pytest.fixture
def denied(monkeypatch):
    state = {"now": 1000.0, "key": "private-fixture-key", "calls": 0, "http": 401}
    monkeypatch.setattr(data, "_BPIQ_AUTH_FAILURE", None)
    monkeypatch.setattr(data, "_BPIQ_CATALYST_STATUS", {})
    monkeypatch.setattr(data, "_BPIQ_CATALYST_CACHE", {"STALE": [{"drug_name": "Not current"}]})
    monkeypatch.setattr(data, "_BPIQ_CACHE_TIMESTAMP", 0)
    monkeypatch.setattr(data, "time", SimpleNamespace(time=lambda: 10000,
                                                      monotonic=lambda: state["now"]))
    monkeypatch.setattr(data, "_get_config_value", lambda key: state["key"])
    def get(*args, **kwargs):
        state["calls"] += 1
        return SimpleNamespace(status_code=state["http"], json=lambda: {"results": []})
    monkeypatch.setattr(data, "rate_limited_get", get)
    return state


@pytest.mark.parametrize("status", [401, 403])
def test_denied_account_is_not_retried_for_every_ticker(denied, status):
    denied["http"] = status
    for _ in range(20):
        assert data._load_bpiq_catalyst_cache() == {}
    assert denied["calls"] == 1
    assert data._BPIQ_CATALYST_STATUS["http_status"] == status
    assert data._BPIQ_CATALYST_STATUS["using_stale_cache"] is False
    assert "STALE" in data._BPIQ_CATALYST_CACHE  # Preserve, do not silently republish.
    assert data._BPIQ_CACHE_TIMESTAMP == 0
    assert "private-fixture" not in json.dumps(data._BPIQ_CATALYST_STATUS)


def test_auth_retry_has_bounded_delay_and_never_refreshes_success_clock(denied):
    data._load_bpiq_catalyst_cache()
    denied["now"] += data._BPIQ_AUTH_RETRY_SECONDS - 1
    data._load_bpiq_catalyst_cache()
    assert denied["calls"] == 1
    denied["now"] += 1
    data._load_bpiq_catalyst_cache()
    assert denied["calls"] == 2 and data._BPIQ_CACHE_TIMESTAMP == 0


def test_corrected_key_is_not_held_by_previous_auth_backoff(denied):
    data._load_bpiq_catalyst_cache()
    denied["key"] = "new-private-fixture-key"
    data._load_bpiq_catalyst_cache()
    assert denied["calls"] == 2


def test_concurrent_consumers_share_one_auth_attempt(denied):
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: data._load_bpiq_catalyst_cache(), range(24)))
    assert results == [{}] * 24 and denied["calls"] == 1


def test_missing_key_never_calls_provider_or_exposes_old_calendar(denied):
    data._load_bpiq_catalyst_cache()
    denied["key"] = None
    assert data._load_bpiq_catalyst_cache() == {}
    assert denied["calls"] == 1
    assert data._BPIQ_CATALYST_STATUS["http_status"] is None


def test_valid_response_recovers_after_auth_retry_without_serving_old_rows(denied, monkeypatch):
    data._load_bpiq_catalyst_cache()
    denied["now"] += data._BPIQ_AUTH_RETRY_SECONDS
    def recovered(*args, **kwargs):
        denied["calls"] += 1
        return SimpleNamespace(status_code=200, json=lambda: {"results": [{
            "ticker": "RECOVERED", "drug_name": "Valid calendar row",
            "stage_event": {"stage_label": "Phase 3"}, "catalyst_date": "2026-12-01",
        }]})
    monkeypatch.setattr(data, "rate_limited_get", recovered)
    result = data._load_bpiq_catalyst_cache()
    assert set(result) == {"RECOVERED"}
    assert data._BPIQ_CATALYST_STATUS["status"] == "success"
    assert data._BPIQ_AUTH_FAILURE is None
    assert data._load_bpiq_catalyst_cache() == result
    assert denied["calls"] == 2
