"""Real Turtle producer adjustment proof; only synthetic providers and caches.

The custom-bars query defaults to adjusted=True and its response flag denotes
split adjustment: https://massive.com/docs/rest/stocks/aggregates/custom-bars
Explicit contradictory metadata is not adjusted historical evidence. A missing
legacy flag stays compatible with the explicit adjusted request.
"""
from copy import deepcopy

import pytest

import api
from test_cross_scanner_reaudit_regressions import grouped_reply
from test_scanner_reaudit_regressions import freeze, NOW, Response
from test_stock_audit_repair_contracts import daily_bars, snapshot


MISSING = object()


def run_producer(monkeypatch, *, adjustment=MISSING, saved=None, requests=None):
    freeze(monkeypatch, NOW.replace(hour=9, minute=0))
    bars = daily_bars(30, session="2026-09-29")
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3_000_000.)
    saved = [] if saved is None else saved
    requests = [] if requests is None else requests

    def provider(url, **kwargs):
        grouped = grouped_reply(url, bars)
        if grouped is not None:
            return grouped
        if "/aggs/ticker/" in url:
            requests.append(deepcopy(kwargs.get("params")))
            payload = {"status": "DELAYED", "results": deepcopy(bars)}
            if adjustment is not MISSING:
                payload["adjusted"] = adjustment
            return Response(payload)
        return Response(snapshot() if url.endswith("/tickers") else {"tickers": []})

    monkeypatch.setattr(api, "rate_limited_get", provider)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **kw: ({"AUDT"}, "fixture"))
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: saved.extend(deepcopy(rows)))
    api._turtle_scan_wrapper()
    return saved, requests


@pytest.mark.parametrize("adjustment", [False, None, 0, 1, "true", "false", {}, []])
def test_explicit_unadjusted_or_incompatible_history_cannot_publish_or_reach_scoring(monkeypatch, adjustment):
    from modules import stock_bars
    monkeypatch.setattr(stock_bars, "completed_polygon_bars",
        lambda *a, **k: pytest.fail("Contradictory adjustment must stop before historical scoring"))
    saved, requests = [], []
    with pytest.raises(api.ScannerDataError) as failure:
        run_producer(monkeypatch, adjustment=adjustment, saved=saved, requests=requests)
    assert failure.value.code == "scan_data_incomplete"
    assert failure.value.diagnostics == {
        "scanner": "turtle", "coverage": "incomplete", "final_results": None,
        "reason": "history_adjustment_incompatible"}
    assert saved == []
    assert requests and requests[0]["adjusted"] == "true"


@pytest.mark.parametrize("adjustment", [True, MISSING], ids=["confirmed-adjusted", "legacy-missing-flag"])
def test_explicit_request_and_compatible_history_keep_real_turtle_plan(monkeypatch, adjustment):
    rows, requests = run_producer(monkeypatch, adjustment=adjustment)
    assert len(rows) == len(requests) == 1
    assert requests[0]["adjusted"] == "true"
    row = rows[0]
    assert row["Preis"] == row["swing_reference_close"] == 100.1
    assert row["DC_High_20"] == 100.
    assert row["StopLoss"] < row["Entry"] < row["TP1"] < row["TP2"]
    assert row["Trade_Setup_Source"] == "turtle_r_multiple"
    assert row["fill_evidence_verified"] is False
    assert api.stock_swing.validate(row, NOW.replace(hour=9, minute=0))


def test_metadata_proof_does_not_change_existing_donchian_or_risk_geometry():
    with pytest.MonkeyPatch.context() as monkeypatch:
        legacy, _ = run_producer(monkeypatch)
    with pytest.MonkeyPatch.context() as monkeypatch:
        explicit, _ = run_producer(monkeypatch, adjustment=True)
    assert explicit == legacy
