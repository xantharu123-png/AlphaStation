"""Read-only collector diagnostics; no application import or external service."""
import hashlib
import json

import pytest

from scripts import collect_server_evidence as collector


def fixture_row():
    return {
        "ticker": "PRIVATE_SYMBOL", "email": "PRIVATE_EMAIL",
        "native_plan_reason": "first_opposing_barrier_before_minimum_rr",
        "score": 91, "price": 100.9,
        "trade_setup": {
            "entry": 100.9, "stop": 95, "tp1": 101.2, "rr_tp1": .05,
            "direction": "LONG", "structure_status": "WAIT_BREAK_RECLAIM",
            "notes": "SECRET_NOTES",
            "nearest_barrier": {"zone_id": "private-id", "zone_low": 100.7,
                "zone_high": 101.2, "distance_r": 0., "overlapping": True,
                "timeframe": "1D/4H", "source": "SECRET_FREE_TEXT",
                "confirmed_at": "2026-09-24T20:00:00Z"}},
        "level_structure": {"as_of": "2026-09-24T20:00:00Z", "zones": [
            {"zone_id": "private-id", "sources": ["PDC", "PDH", "SECRET_SOURCE"],
             "break_state": "break_confirmed", "evidence": "SECRET_HISTORY"}]}}


def test_geometry_is_bounded_private_projection_not_delivery(tmp_path):
    path = tmp_path / "strategy_momentum_breakout_long_cache.json"
    path.write_text(json.dumps({"results": [fixture_row()] * 53}), encoding="utf8")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = collector.safe_cache_summary(path)["stock_plan_diagnostics"]
    assert (result["rows_total"], result["rows_sampled"], result["rows_omitted"]) == (53, 50, 3)
    assert result["semantics"] == "cached_geometry_not_fresh_approval_or_delivery"
    row = result["rows"][0]
    assert row["plan"] == {"entry": 100.9, "stop": 95, "tp1": 101.2, "rr_tp1": .05}
    assert row["barrier"]["sources"] == ["PDC", "PDH"]
    assert row["barrier"]["unrecognized_source_count"] == 1
    assert row["barrier"]["timeframes"] == ["1D", "4H"]
    assert row["barrier"]["confirmed_at"] == "2026-09-24T20:00:00+00:00"
    for secret in ("PRIVATE", "SECRET", "private-id"):
        assert secret not in json.dumps(result)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("value", [True, "PRIVATE", float("inf"), float("nan"), 10**400, {}, []])
def test_untrusted_numeric_and_enum_fields_are_never_coerced(value):
    row = fixture_row()
    row.update(score=value, native_plan_reason=value)
    row["trade_setup"].update(entry=value, structure_status=value)
    result = collector._stock_plan_projection([row])["rows"][0]
    assert "score" not in result and "entry" not in result["plan"]
    assert result["native_plan_reason"] == result["structure_status"] == "unknown"


def test_no_unbound_or_ambiguous_zone_source_inference():
    raw = fixture_row()
    raw["level_structure"]["zones"] *= 2
    projected = collector._stock_plan_projection([raw])["rows"][0]
    assert "sources" not in projected["barrier"]
    raw["level_structure"]["zones"] = []
    assert "sources" not in collector._stock_plan_projection([raw])["rows"][0]["barrier"]


def test_blocked_builder_barrier_without_native_plan_is_preserved():
    row = {"native_plan_diagnostics": {"reason": "crossed_resistance_unconfirmed",
           "barrier": {"zone_low": 90, "zone_high": 95, "confirmed_at": "SECRET_DATE"}}}
    result = collector._stock_plan_projection([row, None])
    assert result["rows"][0]["barrier"] == {"zone_low": 90, "zone_high": 95}
    assert result["rows"][0]["native_plan_reason"] == "crossed_resistance_unconfirmed"
    assert result["rows"][1] == {"row_index": 1, "invalid_row": True}


def test_larger_stock_cache_read_is_named_bounded_and_never_exports_padding(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "CACHE_MAX_BYTES", 128)
    monkeypatch.setattr(collector, "STOCK_CACHE_MAX_BYTES", 4096)
    payload = json.dumps({"results": [fixture_row()], "private_padding": "SECRET" * 100})
    stock = tmp_path / "strategy_momentum_breakout_long_cache.json"
    generic = tmp_path / "cache.json"
    for path in (stock, generic):
        path.write_text(payload, encoding="utf8")
    summary = collector.safe_cache_summary(stock)
    assert summary["available"] is True
    assert "SECRET" not in json.dumps(summary)
    assert collector.safe_cache_summary(generic) == {"available": False, "reason": "too_large"}
    monkeypatch.setattr(collector, "STOCK_CACHE_MAX_BYTES", 128)
    assert collector.safe_cache_summary(stock) == {"available": False, "reason": "too_large"}


def test_non_stock_cache_does_not_gain_geometry_projection(tmp_path):
    path = tmp_path / "crypto_explosion_cache.json"
    path.write_text(json.dumps({"results": [fixture_row()]}), encoding="utf8")
    assert "stock_plan_diagnostics" not in collector.safe_cache_summary(path)


def test_watch_no_recipients_is_known_diagnostic_reason():
    assert "watch_no_eligible_recipients" in collector.SUPPRESSION_REASONS
