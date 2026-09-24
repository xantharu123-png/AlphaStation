"""Finished BI exclusions reach normal per-row gates, never bypass them."""
import json

import pytest

import api
from modules import scanners
from test_bi_badbar_retry import bad
from test_bi_diagnostics_integration import _result
from test_bi_signal_contract_downstream import _bi_row, _patch_result_decoration
from test_bi_transport_recovery import Reply, setup, valid


def _wrapper_io(monkeypatch, tmp_path, replies, direction):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, replies, count=2, direction=direction)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **k: _result(17))
    monkeypatch.setattr(api, "BI_CACHE_LONG" if direction == "long" else "BI_CACHE_SHORT", str(final))
    monkeypatch.setitem(api.SCAN_CACHE_MAP, "bi_" + direction, str(final))
    monkeypatch.setitem(api._SCAN_PROGRESS_MAP, "bi_" + direction, str(tmp_path / (direction + "-progress.json")))
    monkeypatch.setattr(api, "_bi_background_scan", lambda key, direction, candidates: scanners._bi_background_scan(key, direction, tickers))
    checks = []
    monkeypatch.setattr(api, "_check_and_alert", lambda name, path: checks.append((name, json.loads(final.read_text()))))
    return tickers, final, calls, checks


@pytest.mark.parametrize("direction", ["long", "short"])
def test_finished_approved_exclusion_reaches_existing_per_row_mail_check(monkeypatch, tmp_path, direction):
    tickers, final, calls, checks = _wrapper_io(monkeypatch, tmp_path, [bad(), bad(), valid()], direction)
    api._bi_background_scan_wrapper(direction)
    assert len(checks) == 1 and checks[0][0] == "bi_" + direction
    cache = checks[0][1]
    assert cache["diagnostics"]["coverage"] == "complete_with_exclusions"
    assert cache["partial"] is False and cache["checked"] == cache["total"] == 2
    assert [r["Ticker"] for r in cache["results"]] == [tickers[1]]
    assert cache["results"][0]["BI_IndicatorsGreen"] >= 17
    assert not final.with_name(final.name + ".partial").exists()


@pytest.mark.parametrize("terminal", ["global_error", "stopped"])
def test_unfinished_wrapper_never_reaches_mail_check_even_after_valid_row(monkeypatch, tmp_path, terminal):
    tickers, final, calls, checks = _wrapper_io(monkeypatch, tmp_path, [valid(), Reply(401)], "long")
    if terminal == "stopped":
        monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: len(calls) >= 1)
    with pytest.raises((scanners.ScannerDataError, RuntimeError)):
        api._bi_background_scan_wrapper("long")
    assert checks == [] and final.read_text() == "previous-final"
    assert not final.with_name(final.name + ".partial").exists()


@pytest.mark.parametrize("endpoint", ["dedicated", "generic"])
def test_api_preserves_exclusion_scope_and_warns_without_resurrecting_sub17_rows(monkeypatch, endpoint):
    d = dict(coverage="complete_with_exclusions", checked=2, total=2,
             valid_data_symbols=1, excluded_data_symbols=1, final_results=1)
    rows = [_bi_row("VALID"), _bi_row("INVALID", green=16)]
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: (rows, None, {"diagnostics": d, "checked": 2, "total": 2, "partial": False}, False))
    _patch_result_decoration(monkeypatch)
    response = (api.get_bi_results(direction="long") if endpoint == "dedicated"
                else api.get_scan_results(direction="long", market_type="stocks"))
    assert [row["ticker"] for row in response.data] == ["VALID"]
    assert response.count == 1 and response.partial is False
    diagnostics = response.diagnostics["funnel"] if endpoint == "dedicated" else response.diagnostics
    assert diagnostics["coverage"] == "complete_with_exclusions" and diagnostics["excluded_data_symbols"] == 1
    assert any("1 Aktie(n) in diesem Lauf" in text
               and "Keine dauerhafte Sperre" in text
               and "im naechsten Scan erneut geprueft" in text
               and "keine vollstaendige Datenabdeckung" in text
               for text in response.warnings)


@pytest.mark.parametrize("value", [None, {}, {"coverage": "incomplete", "excluded_data_symbols": 1},
    {"coverage": "complete_with_exclusions", "excluded_data_symbols": True},
    {"coverage": "complete_with_exclusions", "excluded_data_symbols": "PRIVATE"}])
def test_exclusion_warning_does_not_echo_malformed_or_unfinished_metadata(value):
    assert api._bi_data_exclusion_warning(value) is None
