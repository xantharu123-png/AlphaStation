"""Offline regressions for the corrected crypto volume-profile cache boundary."""
from copy import deepcopy
from datetime import datetime

import pytest

import api


def _current_row(*, short=False):
    side = "SHORT" if short else "LONG"
    row = {
        "symbol": "TESTUSDT", "Symbol": "TEST", "exchange": "bybit",
        "contract_symbol": "TESTUSDT", "direction": side,
        "price": 100, "entry": 100, "stop": 110 if short else 90,
        "tp1": 80 if short else 120, "tp2": 70 if short else 130,
        "trade_action": "SHORT_NOW" if short else "LONG_NOW",
        "trade_signal": "JETZT_TRADEN", "alertable_crypto": True,
        "execution_trigger_ok": True, "execution_data_age_seconds": 20,
        "target_quality": "STRUCTURAL", "tp1_is_projection": False,
        "scan_price_observed_at": 1000, "scan_price_source": "bybit:closed_5m",
    }
    # A fixture of a newly computed producer, not a migration of old evidence.
    api._stamp_fresh_crypto_profile_contract(row, new_listing=short)
    return row


@pytest.mark.parametrize("version", [None, 0, "1", True, 999])
def test_legacy_mail_contract_rejected_before_any_quote_or_path_io(monkeypatch, version):
    row = dict(symbol="TESTUSDT", exchange="bybit", price=100, entry=100,
               stop=90, tp1=120, tp2=130, scan_price_observed_at=1000,
               scan_price_source="bybit:closed_5m")
    if version is not None:
        row["crypto_profile_cache_version"] = version
    before = deepcopy(row)
    monkeypatch.setattr(api, "_fetch_crypto_executable_quote", lambda *_a, **_k: pytest.fail("old profile reached quote IO"))
    result = api._revalidate_crypto_trade_mail_candidate(row, direction="LONG", now_ts=1010)
    assert result == {"ok": False, "reason": "crypto_profile_cache_version_old_scan_again"}
    assert row == before


def test_row_marker_cannot_launder_an_old_nested_profile():
    row = _current_row()
    row["trade_setup"] = {
        "crypto_profile_cache_version": api._CRYPTO_PROFILE_CACHE_VERSION,
        "vrvp_levels": {"poc": 100, "range_low": 90, "range_high": 99999},
    }
    before = deepcopy(row)
    assert api._crypto_profile_contract_reason(row)
    assert row == before


def _stub_result_paths(monkeypatch, rows, metadata):
    cached_at = datetime.now().isoformat()
    monkeypatch.setattr(api, "load_cache_file", lambda *_a, **_k: (deepcopy(rows), cached_at))
    monkeypatch.setattr(api, "load_cache_metadata", lambda *_a: deepcopy(metadata))
    monkeypatch.setattr(api, "load_live_cache_file", lambda *_a, **_k: (deepcopy(rows), cached_at, deepcopy(metadata), False))
    monkeypatch.setattr(api, "_scan_cache_payload", lambda *_a: deepcopy(metadata))
    monkeypatch.setattr(api, "_decorate_scan_results", lambda values, *_a: values)
    monkeypatch.setattr(api, "_decorate_early_mover_results", lambda values, *_a: values)
    monkeypatch.setattr(api, "_decorate_new_listing_display_results", lambda values, *_a: (values, {}))
    monkeypatch.setattr(api, "_downgrade_expired_crypto_triggers", lambda values, *_a: values)
    monkeypatch.setattr(api, "_downgrade_expired_new_listing_triggers", lambda values, *_a: values)
    monkeypatch.setattr(api, "_apply_scanner_visibility_policy", lambda _scanner, values: values)
    monkeypatch.setattr(api, "_apply_signal_only_policy", lambda _scanner, values: values)
    monkeypatch.setattr(api, "_scan_quality_payload", lambda *_a: {"warnings": [], "exclusion_policy": {}})
    monkeypatch.setattr(api, "_merge_crypto_trade_signals", lambda longs, shorts, **_k: longs + shorts)
    return cached_at


@pytest.mark.parametrize("getter", ["get_early_movers", "get_crypto_explosion_results", "get_new_listing_results"])
def test_gets_reject_old_cache_before_decorating(monkeypatch, getter):
    row = _current_row(short=getter == "get_new_listing_results")
    _stub_result_paths(monkeypatch, [row], {})
    response = getattr(api, getter)()
    assert response["data"] == []
    assert "crypto_profile_cache_version_old_scan_again" in response["warnings"]
    assert response["data_quality"]["cache_status"] == "stale"


def test_combined_cannot_promote_old_source_cache_even_if_row_looks_current(monkeypatch):
    row = _current_row()
    _stub_result_paths(monkeypatch, [row], {})
    rows, stats, *_rest, warnings = api._build_crypto_trade_signals_from_caches(display_only=True)
    assert rows == []
    assert stats["trade_now_count"] == 0
    assert "crypto_profile_cache_version_old_scan_again" in warnings


@pytest.mark.parametrize("getter", ["get_early_movers", "get_crypto_explosion_results", "get_new_listing_results"])
def test_new_cache_contract_preserves_existing_prices_and_flags(monkeypatch, getter):
    short = getter == "get_new_listing_results"
    row = _current_row(short=short)
    before = deepcopy(row)
    _stub_result_paths(monkeypatch, [row], api._crypto_profile_cache_metadata(new_listing=short))
    response = getattr(api, getter)()
    assert response["data"] == [before]
    assert row == before
    assert "crypto_profile_cache_version_old_scan_again" not in response["warnings"]


def test_new_listing_v2_is_incompatible_even_with_new_volume_contract(monkeypatch):
    row = _current_row(short=True)
    row["new_listing_short_cache_version"] = 2
    _stub_result_paths(monkeypatch, [row], api._crypto_profile_cache_metadata(new_listing=True))
    assert api.get_new_listing_results()["data"] == []
    assert api._NEW_LISTING_SHORT_CACHE_VERSION == 3


def test_early_final_revalidator_rejects_legacy_before_shared_gate(monkeypatch):
    row = _current_row()
    row.pop("crypto_profile_cache_version")
    monkeypatch.setattr(api, "_revalidate_crypto_trade_mail_candidate", lambda *_a, **_k: pytest.fail("legacy reached shared revalidation"))
    assert api._revalidate_early_mover_mail_candidate(row)["ok"] is False


def test_new_listing_final_revalidator_rejects_v2_before_shared_gate(monkeypatch):
    row = _current_row(short=True)
    row["new_listing_short_cache_version"] = 2
    alert = {**row, "source_row": row}
    monkeypatch.setattr(api, "_revalidate_crypto_trade_mail_candidate", lambda *_a, **_k: pytest.fail("legacy reached shared revalidation"))
    assert api._revalidate_new_listing_mail_candidate(alert)["ok"] is False


def test_fresh_producer_stamp_is_metadata_only_and_covers_nested_profile():
    row = dict(entry=100, stop=90, tp1=120, tp2=130, accepted=False,
               trade_setup={"vrvp_levels": {"supports": [90], "resistances": [120]}},
               trade_action="WAIT_FOR_RETEST", score=86)
    before = deepcopy(row)
    api._stamp_fresh_crypto_profile_contract(row)
    assert api._crypto_profile_contract_reason(row) is None
    assert row["crypto_profile_cache_version"] == api._CRYPTO_PROFILE_CACHE_VERSION
    for key in ("entry", "stop", "tp1", "tp2", "accepted", "trade_action", "score"):
        assert row[key] == before[key]
    assert row["trade_setup"]["vrvp_levels"]["resistances"] == [120]


def test_early_fresh_wrapper_publishes_version_not_read_migration(monkeypatch):
    row = dict(Symbol="TEST", Price=100, trade_setup=dict(entry=100, stop=90, tp1=120, tp2=130))
    payload = {"coins": [row], "stats": {}}
    writes = []
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    monkeypatch.setattr(api, "fetch_early_movers", lambda **_k: payload)
    monkeypatch.setattr(api, "_scan_control_point", lambda **_k: None)
    monkeypatch.setattr(api, "_remove_partial_cache", lambda *_a: None)
    monkeypatch.setattr(api, "finalize_cache_file", lambda *args, **kwargs: writes.append((deepcopy(args), deepcopy(kwargs))))
    monkeypatch.setattr(api, "load_cache_file", lambda *_a: ([], None))
    monkeypatch.setattr(api, "_send_early_mover_long_alerts", lambda *_a: False)
    api._early_movers_wrapper()
    args, kwargs = writes[0]
    assert kwargs["metadata"]["crypto_profile_cache_version"] == api._CRYPTO_PROFILE_CACHE_VERSION
    assert api._crypto_profile_contract_reason(args[1][0]["coins"][0]) is None


def test_explosion_fresh_wrapper_publishes_version_and_preserves_scan_stats(monkeypatch):
    row = dict(symbol="TEST", entry=100, stop=90, tp1=120, tp2=130)
    stats = {"result_count": 1, "source_degraded": True}
    writes = []
    monkeypatch.setattr(api, "_run_crypto_explosion_scan", lambda: ([row], stats))
    monkeypatch.setattr(api, "_scan_control_point", lambda **_k: None)
    monkeypatch.setattr(api, "_ce_progress_update", lambda **_k: None)
    monkeypatch.setattr(api, "save_cache_file", lambda *args, **kwargs: writes.append((deepcopy(args), deepcopy(kwargs))))
    api._crypto_explosion_wrapper()
    args, kwargs = writes[0]
    assert kwargs["metadata"]["crypto_profile_cache_version"] == api._CRYPTO_PROFILE_CACHE_VERSION
    assert kwargs["metadata"]["scan_stats"] == stats
    assert api._crypto_profile_contract_reason(args[1][0]) is None


def test_new_listing_fresh_wrapper_marks_only_fresh_producer_output(monkeypatch):
    sig = dict(direction="SHORT", entry=100, stop=110, tp1=80, tp2=70,
               trade_setup={"entry": 100, "stop": 110, "tp1": 80, "tp2": 70})
    payload = {"signals": [{"symbol": "TESTUSDT", "exchange": "bybit", "signal": sig}],
               "watchlist": [], "monitoring": []}
    writes, mails = [], []
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "seed_instrument_cache", lambda: None)
    monkeypatch.setattr(api, "run_new_listing_scanner", lambda: payload)
    monkeypatch.setattr(api, "save_cache_file", lambda *args, **kwargs: writes.append((deepcopy(args), deepcopy(kwargs))))
    monkeypatch.setattr(api, "_send_new_listing_pipeline_alerts", lambda values: mails.append(deepcopy(values)))
    api._new_listing_wrapper()
    args, kwargs = writes[0]
    assert kwargs["metadata"] == api._crypto_profile_cache_metadata(new_listing=True)
    assert api._crypto_profile_contract_reason(args[1][0], new_listing=True) is None
    assert api._crypto_profile_contract_reason(mails[0]["signals"][0]["signal"], new_listing=True) is None


@pytest.mark.parametrize("name", ["early_movers", "crypto_explosion", "new_listing", "crypto_trade_signals"])
@pytest.mark.parametrize("current", [False, True])
def test_incompatible_cache_cannot_defer_startup_recalculation(monkeypatch, name, current):
    row = _current_row(short=name == "new_listing")
    metadata = api._crypto_profile_cache_metadata(new_listing=name == "new_listing") if current else {}
    payload = {**metadata, "results": [row]}
    monkeypatch.setattr(api.os.path, "getmtime", lambda *_a: 999)
    monkeypatch.setattr(api, "_scan_cache_payload", lambda *_a: payload)
    assert api._startup_scan_cache_time(name, 1000) == (999 if current else None)


def test_startup_current_metadata_cannot_launder_legacy_row(monkeypatch):
    row = _current_row()
    row.pop("crypto_profile_cache_version")
    payload = {**api._crypto_profile_cache_metadata(), "results": [row]}
    monkeypatch.setattr(api.os.path, "getmtime", lambda *_a: 999)
    monkeypatch.setattr(api, "_scan_cache_payload", lambda *_a: payload)
    assert api._startup_scan_cache_time("crypto_explosion", 1000) is None


def test_reflattening_legacy_new_listing_payload_does_not_assign_current_version():
    payload = {"signals": [{"symbol": "TESTUSDT", "exchange": "bybit", "signal": {
        "direction": "SHORT", "entry": 100, "stop": 110, "tp1": 80, "tp2": 70,
        "new_listing_short_cache_version": 2, "target_quality": "STRUCTURAL",
    }}]}
    row = api._flatten_new_listing_pipeline_results(payload)[0]
    assert row["new_listing_short_cache_version"] == 2
    assert row["crypto_profile_cache_version"] is None
    assert row["source_trade_contract_validated"] is False
    assert row["trade_action"] != "SHORT_NOW"


@pytest.mark.parametrize("key", ["coins", "signals", "watchlist", "monitoring"])
def test_current_container_cannot_hide_legacy_rows_under_nonflat_schema(key):
    container = {**api._crypto_profile_cache_metadata(), key: [{"symbol": "LEGACY", "tp1": 99999}]}
    before = deepcopy(container)
    rows, reason = api._compatible_crypto_cache_results([container], api._crypto_profile_cache_metadata())
    assert rows == []
    assert reason == "crypto_profile_cache_version_old_scan_again"
    assert container == before
